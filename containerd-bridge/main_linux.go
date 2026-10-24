//go:build linux

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	containerd "github.com/containerd/containerd"
	taskapi "github.com/containerd/containerd/api/services/tasks/v1"
	"github.com/containerd/containerd/cio"
	"github.com/containerd/containerd/containers"
	"github.com/containerd/containerd/contrib/seccomp"
	"github.com/containerd/containerd/errdefs"
	"github.com/containerd/containerd/namespaces"
	"github.com/containerd/containerd/oci"
	"github.com/containerd/containerd/snapshots"
	gocni "github.com/containerd/go-cni"
	"github.com/containerd/typeurl/v2"
	specs "github.com/opencontainers/runtime-spec/specs-go"
	"golang.org/x/sys/unix"
)

type networkState struct {
	IP string `json:"ip"`
}

func main() {
	var r request
	if err := json.NewDecoder(os.Stdin).Decode(&r); err != nil {
		emit(nil, err)
		return
	}
	if err := validate(r); err != nil {
		emit(nil, err)
		return
	}
	ctx, cancel := context.WithTimeout(namespaces.WithNamespace(context.Background(), r.Namespace),
		time.Duration(r.Timeout*float64(time.Second)))
	defer cancel()
	client, err := containerd.New(r.Socket)
	if err != nil {
		emit(nil, err)
		return
	}
	defer client.Close()
	result, err := dispatch(ctx, client, r)
	emit(result, err)
}

func emit(result map[string]interface{}, err error) {
	if err != nil {
		// Never echo input, tokens, registry credentials, or upstream error payloads.
		result = map[string]interface{}{"error": "containerd operation failed"}
	}
	if result == nil {
		result = map[string]interface{}{}
	}
	_ = json.NewEncoder(os.Stdout).Encode(result)
}

func owned(ctx context.Context, client *containerd.Client, id string) (containerd.Container, error) {
	c, err := client.LoadContainer(ctx, id)
	if err != nil {
		return nil, err
	}
	info, err := c.Info(ctx)
	if err != nil {
		return nil, err
	}
	if info.Labels["europa.runtime_id"] != id {
		return nil, errors.New("ownership mismatch")
	}
	return c, nil
}

func dispatch(ctx context.Context, client *containerd.Client, r request) (map[string]interface{}, error) {
	switch r.Operation {
	case "managed_inventory":
		// Admission starts only from an empty managed namespace. Never delete
		// unknown allocations or infer that untracked workloads have no cost.
		cs, err := client.Containers(ctx)
		if err != nil {
			return nil, err
		}
		ts, err := client.TaskService().List(ctx, &taskapi.ListTasksRequest{})
		if err != nil {
			return nil, err
		}
		count := 0
		err = client.SnapshotService("overlayfs").Walk(ctx, func(ctx context.Context, info snapshots.Info) error {
			if info.Labels["europa.runtime_id"] != "" || strings.HasSuffix(info.Name, "-rootfs") {
				count++
			}
			return nil
		})
		if err != nil {
			return nil, err
		}
		entries, err := os.ReadDir(r.StateRoot)
		if err != nil && !os.IsNotExist(err) {
			return nil, err
		}
		return map[string]interface{}{"containers": len(cs), "tasks": len(ts.Tasks),
			"snapshots": count, "state_directories": len(entries)}, nil
	case "image":
		started := time.Now()
		image, err := client.GetImage(ctx, r.Image)
		cached := err == nil
		if errdefs.IsNotFound(err) {
			image, err = client.Pull(ctx, r.Image)
		}
		if err != nil {
			return nil, err
		}
		availability := time.Since(started).Seconds()
		started = time.Now()
		unpacked, err := image.IsUnpacked(ctx, "overlayfs")
		if err != nil {
			return nil, err
		}
		if !unpacked {
			err = image.Unpack(ctx, "overlayfs")
		}
		return map[string]interface{}{"cached": cached, "availability_seconds": availability,
			"unpack_seconds": time.Since(started).Seconds()}, err
	case "create":
		image, err := client.GetImage(ctx, r.Image)
		if err != nil {
			return nil, err
		}
		_, err = client.NewContainer(ctx, r.ID,
			containerd.WithImage(image), containerd.WithSnapshotter("overlayfs"),
			containerd.WithNewSnapshot(r.ID+"-rootfs", image, snapshots.WithLabels(map[string]string{"europa.runtime_id": r.ID})),
			containerd.WithRuntime(r.Handler, nil),
			containerd.WithContainerLabels(map[string]string{"europa.runtime_id": r.ID, "europa.notebook_id": r.NotebookID}),
			containerd.WithNewSpec(oci.WithImageConfig(image), seccomp.WithDefaultProfile(), func(ctx context.Context, client oci.Client, c *containers.Container, s *specs.Spec) error {
				harden(s, r.Resources)
				return nil
			}),
		)
		return map[string]interface{}{}, err
	case "start":
		return startTask(ctx, client, r)
	case "inspect":
		c, err := owned(ctx, client, r.ID)
		if errdefs.IsNotFound(err) {
			return map[string]interface{}{"state": "absent"}, nil
		}
		if err != nil {
			return nil, err
		}
		task, err := c.Task(ctx, nil)
		if errdefs.IsNotFound(err) {
			return map[string]interface{}{"state": "stopped"}, nil
		}
		if err != nil {
			return nil, err
		}
		status, err := task.Status(ctx)
		if err != nil {
			return nil, err
		}
		result := map[string]interface{}{"state": string(status.Status), "exit_code": status.ExitStatus,
			"limits_configured": true}
		spec, specErr := c.Spec(ctx)
		if specErr == nil && status.Status == containerd.Running {
			for key, value := range cgroupObservation(task.Pid(), spec) {
				result[key] = value
			}
		}
		return result, nil
	case "stop":
		return stopTask(ctx, client, r)
	case "kill", "pause":
		c, err := owned(ctx, client, r.ID)
		if err != nil {
			return nil, err
		}
		task, err := c.Task(ctx, nil)
		if err != nil {
			return nil, err
		}
		sig := unix.SIGKILL
		if r.Operation == "pause" {
			sig = unix.SIGSTOP
		}
		return map[string]interface{}{}, task.Kill(ctx, sig, containerd.WithKillAll)
	case "destroy":
		stopped, err := stopTask(ctx, client, r)
		if err != nil {
			return nil, err
		}
		c, err := owned(ctx, client, r.ID)
		if err == nil {
			err = c.Delete(ctx, containerd.WithSnapshotCleanup)
		}
		if err != nil && !errdefs.IsNotFound(err) {
			return nil, err
		}
		// NewContainer can fail after preparing a snapshot but before metadata commit.
		snap := client.SnapshotService("overlayfs")
		info, err := snap.Stat(ctx, r.ID+"-rootfs")
		if err == nil {
			if info.Labels["europa.runtime_id"] != r.ID {
				return nil, errors.New("snapshot ownership mismatch")
			}
			if err = snap.Remove(ctx, r.ID+"-rootfs"); err != nil {
				return nil, err
			}
		} else if !errdefs.IsNotFound(err) {
			return nil, err
		}
		if err = os.RemoveAll(filepath.Join(r.StateRoot, r.ID)); err != nil {
			return nil, err
		}
		return stopped, nil
	case "inventory":
		containers, err := client.Containers(ctx, "id=="+r.ID)
		if err != nil {
			return nil, err
		}
		tasks, err := client.TaskService().List(ctx, &taskapi.ListTasksRequest{})
		if err != nil {
			return nil, err
		}
		taskCount := 0
		for _, p := range tasks.Tasks {
			if p.ID == r.ID {
				taskCount++
			}
		}
		_, err = client.SnapshotService("overlayfs").Stat(ctx, r.ID+"-rootfs")
		snapshotPresent := err == nil
		if err != nil && !errdefs.IsNotFound(err) {
			return nil, err
		}
		_, err = os.Stat(filepath.Join(r.StateRoot, r.ID))
		return map[string]interface{}{"containers": len(containers), "tasks": taskCount,
			"snapshot_present": snapshotPresent, "network_state_present": err == nil}, nil
	}
	return nil, errors.New("unknown operation")
}

func network(r request) (gocni.CNI, error) {
	data, err := os.ReadFile(r.CNIConfig)
	if err != nil {
		return nil, err
	}
	if err = validateNetwork(data); err != nil {
		return nil, err
	}
	cni, err := gocni.New(gocni.WithPluginDir([]string{r.CNIBin}), gocni.WithMinNetworkCount(2))
	if err != nil {
		return nil, err
	}
	return cni, cni.Load(gocni.WithLoNetwork, gocni.WithConfListFile(r.CNIConfig))
}

func startTask(ctx context.Context, client *containerd.Client, r request) (map[string]interface{}, error) {
	c, err := owned(ctx, client, r.ID)
	if err != nil {
		return nil, err
	}
	s, err := c.Spec(ctx)
	if err != nil {
		return nil, err
	}
	// The only dynamic environment is provider credentials plus locale overrides.
	filtered := []string{}
	for _, value := range s.Process.Env {
		key := strings.SplitN(value, "=", 2)[0]
		if key == "KG_AUTH_TOKEN" {
			continue
		}
		if _, overridden := r.Environment[key]; overridden {
			continue
		}
		filtered = append(filtered, value)
	}
	s.Process.Env = append(filtered, "KG_AUTH_TOKEN="+r.Token)
	for k, v := range r.Environment {
		s.Process.Env = append(s.Process.Env, k+"="+v)
	}
	if err = c.Update(ctx, func(ctx context.Context, client *containerd.Client, info *containers.Container) error {
		value, err := typeurl.MarshalAny(s)
		if err != nil {
			return err
		}
		info.Spec = value
		return nil
	}); err != nil {
		return nil, err
	}
	task, err := c.NewTask(ctx, cio.NullIO)
	if err != nil {
		return nil, err
	}
	dir := filepath.Join(r.StateRoot, r.ID)
	if err = os.MkdirAll(dir, 0700); err != nil {
		return nil, err
	}
	nsPath := filepath.Join(dir, "netns")
	file, err := os.OpenFile(nsPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return nil, err
	}
	file.Close()
	if err = unix.Mount(fmt.Sprintf("/proc/%d/ns/net", task.Pid()), nsPath, "", unix.MS_BIND, ""); err != nil {
		return nil, err
	}
	cni, err := network(r)
	if err != nil {
		return nil, err
	}
	result, err := cni.Setup(ctx, r.ID, nsPath)
	if err != nil {
		return nil, err
	}
	address := ""
	for _, intf := range result.Interfaces {
		for _, conf := range intf.IPConfigs {
			if conf.IP.To4() != nil && conf.IP.IsPrivate() && !conf.IP.IsLoopback() {
				address = conf.IP.String()
			}
		}
	}
	if address == "" {
		return nil, errors.New("private IPv4 address required")
	}
	data, _ := json.Marshal(networkState{address})
	if err = os.WriteFile(filepath.Join(dir, "network.json"), data, 0600); err != nil {
		return nil, err
	}
	if err = task.Start(ctx); err != nil {
		return nil, err
	}
	return map[string]interface{}{"private_ip": address}, nil
}

func removeNetwork(ctx context.Context, r request) error {
	dir := filepath.Join(r.StateRoot, r.ID)
	nsPath := filepath.Join(dir, "netns")
	if _, err := os.Stat(nsPath); os.IsNotExist(err) {
		return nil
	}
	cni, err := network(r)
	if err != nil {
		return err
	}
	if err = cni.Remove(ctx, r.ID, nsPath); err != nil {
		return err
	}
	if err = unix.Unmount(nsPath, unix.MNT_DETACH); err != nil && err != unix.EINVAL {
		return err
	}
	return os.RemoveAll(dir)
}

func stopTask(ctx context.Context, client *containerd.Client, r request) (map[string]interface{}, error) {
	forced := false
	c, err := owned(ctx, client, r.ID)
	if err != nil && !errdefs.IsNotFound(err) {
		return nil, err
	}
	if err == nil {
		task, taskErr := c.Task(ctx, nil)
		if taskErr != nil && !errdefs.IsNotFound(taskErr) {
			return nil, taskErr
		}
		if taskErr == nil {
			status, err := task.Status(ctx)
			if err != nil {
				return nil, err
			}
			// Created tasks have not started the Gateway (e.g. failed CNI setup).
			// Delete them through the SDK rather than waiting for a TERM exit.
			if status.Status != containerd.Stopped && status.Status != containerd.Created {
				exits, err := task.Wait(ctx)
				if err != nil {
					return nil, err
				}
				if err = task.Kill(ctx, unix.SIGTERM, containerd.WithKillAll); err != nil && !errdefs.IsNotFound(err) {
					return nil, err
				}
				grace := time.Duration(r.Grace * float64(time.Second))
				if grace <= 0 {
					grace = 5 * time.Second
				}
				select {
				case <-exits:
				case <-time.After(grace):
					forced = true
					if err = task.Kill(ctx, unix.SIGKILL, containerd.WithKillAll); err != nil && !errdefs.IsNotFound(err) {
						return nil, err
					}
					select {
					case <-exits:
					case <-ctx.Done():
						return nil, ctx.Err()
					}
				case <-ctx.Done():
					return nil, ctx.Err()
				}
			}
			// Release networking while the bind-mounted namespace still exists.
			networkErr := removeNetwork(ctx, r)
			_, deleteErr := task.Delete(ctx, containerd.WithProcessKill)
			if errdefs.IsNotFound(deleteErr) {
				deleteErr = nil
			}
			if combined := errors.Join(networkErr, deleteErr); combined != nil {
				return nil, combined
			}
		}
	}
	if err = removeNetwork(ctx, r); err != nil {
		return nil, err
	}
	return map[string]interface{}{"forced": forced}, nil
}

func cgroupObservation(pid uint32, spec *specs.Spec) map[string]interface{} {
	result := map[string]interface{}{"limits_verified": false}
	membership, err := os.ReadFile(fmt.Sprintf("/proc/%d/cgroup", pid))
	if err != nil || spec.Linux == nil || spec.Linux.Resources == nil {
		return result
	}
	for _, line := range strings.Split(string(membership), "\n") {
		if !strings.HasPrefix(line, "0::/") {
			continue
		} // Unified cgroup v2 only.
		root := filepath.Join("/sys/fs/cgroup", filepath.Clean(strings.TrimPrefix(line, "0::")))
		readNumber := func(name string) (int64, error) {
			data, err := os.ReadFile(filepath.Join(root, name))
			if err != nil {
				return 0, err
			}
			return strconv.ParseInt(strings.TrimSpace(string(data)), 10, 64)
		}
		memory, e1 := readNumber("memory.max")
		pids, e2 := readNumber("pids.max")
		usage, e3 := readNumber("memory.current")
		if e3 == nil {
			result["memory_bytes"] = usage
		}
		cpu, e4 := os.ReadFile(filepath.Join(root, "cpu.max"))
		fields := strings.Fields(string(cpu))
		if e1 != nil || e2 != nil || e4 != nil || len(fields) != 2 {
			return result
		}
		quota, e5 := strconv.ParseInt(fields[0], 10, 64)
		period, e6 := strconv.ParseUint(fields[1], 10, 64)
		limits := spec.Linux.Resources
		if e5 == nil && e6 == nil && limits.Memory != nil && limits.Memory.Limit != nil &&
			limits.Pids != nil && limits.CPU != nil && limits.CPU.Quota != nil && limits.CPU.Period != nil {
			result["limits_verified"] = memory == *limits.Memory.Limit && pids == limits.Pids.Limit &&
				quota == *limits.CPU.Quota && period == *limits.CPU.Period
		}
		result["memory_limit"] = memory
		result["pids_limit"] = pids
		return result
	}
	return result
}
