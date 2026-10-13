package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"regexp"
	"strings"

	specs "github.com/opencontainers/runtime-spec/specs-go"
)

type resources struct {
	CPU    float64 `json:"cpu_cores"`
	Memory int64   `json:"memory_bytes"`
	Pids   int64   `json:"pids_limit"`
}
type request struct {
	Operation   string            `json:"operation"`
	ID          string            `json:"id"`
	Socket      string            `json:"socket"`
	Namespace   string            `json:"namespace"`
	CNIConfig   string            `json:"cni_config"`
	CNIBin      string            `json:"cni_bin"`
	StateRoot   string            `json:"state_root"`
	Timeout     float64           `json:"timeout_seconds"`
	Image       string            `json:"image"`
	Handler     string            `json:"handler"`
	NotebookID  string            `json:"notebook_id"`
	Resources   resources         `json:"resources"`
	Token       string            `json:"token"`
	Environment map[string]string `json:"environment"`
	Grace       float64           `json:"grace_seconds"`
}

var identifier = regexp.MustCompile(`^europa-[a-f0-9]{32}$`)
var imageDigest = regexp.MustCompile(`^[^\s@]+@sha256:[a-f0-9]{64}$`)

func validate(r request) error {
	if !identifier.MatchString(r.ID) || r.Namespace != "europa" || r.Timeout <= 0 || r.Timeout > 300 {
		return errors.New("invalid ownership identity or deadline")
	}
	if (r.Operation == "image" || r.Operation == "create") && !imageDigest.MatchString(r.Image) {
		return errors.New("image digest required")
	}
	if r.Operation == "create" && (r.Resources.CPU < 0.001 || r.Resources.CPU > 1024 || r.Resources.Memory <= 0 || r.Resources.Pids <= 0) {
		return errors.New("explicit bounded resources required")
	}
	if r.Operation == "create" && r.Handler != "io.containerd.runc.v2" && r.Handler != "io.containerd.runsc.v1" {
		return errors.New("unsupported runtime handler")
	}
	if r.Operation == "start" && (r.Token == "" || len(r.Token) > 256) {
		return errors.New("token required")
	}
	for k := range r.Environment {
		if k != "LANG" && k != "LC_ALL" {
			return errors.New("environment key forbidden")
		}
	}
	return nil
}

func harden(s *specs.Spec, limits resources) {
	s.Process.User = specs.User{UID: 10001, GID: 10001}
	s.Process.NoNewPrivileges = true
	s.Process.Capabilities = &specs.LinuxCapabilities{}
	s.Process.Cwd = "/workspace"
	s.Root.Readonly = true
	period := uint64(100000)
	quota := int64(limits.CPU * float64(period))
	if s.Linux.Resources == nil {
		s.Linux.Resources = &specs.LinuxResources{}
	}
	s.Linux.Resources.Memory = &specs.LinuxMemory{Limit: &limits.Memory}
	s.Linux.Resources.CPU = &specs.LinuxCPU{Period: &period, Quota: &quota}
	s.Linux.Resources.Pids = &specs.LinuxPids{Limit: limits.Pids}
	// No host bind mounts, sockets, or credentials. Only isolated writable tmpfs.
	for _, target := range []string{"/tmp", "/home/europa", "/workspace", "/run/jupyter"} {
		s.Mounts = append(s.Mounts, specs.Mount{Destination: target, Type: "tmpfs", Source: "tmpfs",
			Options: []string{"nosuid", "nodev", "noexec", "uid=10001", "gid=10001", "mode=0700",
				fmt.Sprintf("size=%d", limits.Memory/4)}})
	}
}

func validateNetwork(data []byte) error {
	var config struct {
		Name    string `json:"name"`
		Plugins []struct {
			Type       string `json:"type"`
			Bridge     string `json:"bridge"`
			Masquerade bool   `json:"ipMasq"`
			IPAM       struct {
				Type   string `json:"type"`
				Ranges [][]struct {
					Subnet string `json:"subnet"`
				} `json:"ranges"`
				Routes []interface{} `json:"routes"`
			} `json:"ipam"`
		} `json:"plugins"`
	}
	if err := json.Unmarshal(data, &config); err != nil {
		return err
	}
	if len(config.Plugins) != 1 || !strings.HasPrefix(config.Name, "europa") {
		return errors.New("private Europa bridge required")
	}
	plugin := config.Plugins[0]
	if plugin.Type != "bridge" || !strings.HasPrefix(plugin.Bridge, "europa") || plugin.Masquerade ||
		plugin.IPAM.Type != "host-local" || len(plugin.IPAM.Routes) != 0 || len(plugin.IPAM.Ranges) != 1 || len(plugin.IPAM.Ranges[0]) != 1 {
		return errors.New("unsupported network or public forwarding configuration")
	}
	_, subnet, err := net.ParseCIDR(plugin.IPAM.Ranges[0][0].Subnet)
	if err != nil || subnet.IP.To4() == nil || !subnet.IP.IsPrivate() {
		return errors.New("private IPv4 subnet required")
	}
	end := append(net.IP(nil), subnet.IP.To4()...)
	for i := range end {
		end[i] |= ^subnet.Mask[i]
	}
	if !end.IsPrivate() {
		return errors.New("subnet extends outside private address space")
	}
	return nil
}
