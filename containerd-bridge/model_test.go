package main

import (
	specs "github.com/opencontainers/runtime-spec/specs-go"
	"testing"
)

func TestHardenSpec(t *testing.T) {
	s := &specs.Spec{Process: &specs.Process{}, Root: &specs.Root{}, Linux: &specs.Linux{}}
	harden(s, resources{CPU: 1.5, Memory: 256 * 1024 * 1024, Pids: 64})
	if s.Process.User.UID != 10001 || !s.Process.NoNewPrivileges || !s.Root.Readonly {
		t.Fatal("missing process/filesystem restrictions")
	}
	if len(s.Process.Capabilities.Bounding) != 0 || len(s.Process.Capabilities.Effective) != 0 {
		t.Fatal("capabilities retained")
	}
	if *s.Linux.Resources.CPU.Quota != 150000 || *s.Linux.Resources.Memory.Limit != 256*1024*1024 || s.Linux.Resources.Pids.Limit != 64 {
		t.Fatal("resource requests did not map to OCI limits")
	}
	if len(s.Mounts) != 4 {
		t.Fatal("unexpected mounts")
	}
	for _, m := range s.Mounts {
		if m.Type != "tmpfs" {
			t.Fatal("host mount found")
		}
	}
}

func TestInvalidRequests(t *testing.T) {
	for _, r := range []request{
		{Operation: "create", ID: "../../foreign", Namespace: "europa", Timeout: 20},
		{Operation: "image", ID: "europa-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", Namespace: "europa", Timeout: 20, Image: "image:latest"},
		{Operation: "start", ID: "europa-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", Namespace: "europa", Timeout: 20},
	} {
		if validate(r) == nil {
			t.Fatal("unsafe request accepted")
		}
	}
}

func TestPrivateNetworkProfile(t *testing.T) {
	valid := []byte(`{"name":"europa-private","plugins":[{"type":"bridge","bridge":"europa0","ipMasq":false,"ipam":{"type":"host-local","ranges":[[{"subnet":"10.77.0.0/16"}]]}}]}`)
	if validateNetwork(valid) != nil {
		t.Fatal("private profile rejected")
	}
	for _, data := range [][]byte{
		[]byte(`{"name":"europa","plugins":[{"type":"portmap"}]}`),
		[]byte(`{"name":"europa","plugins":[{"type":"host-device"}]}`),
		[]byte(`{"name":"europa","plugins":[{"type":"bridge","bridge":"europa0","ipMasq":true}]}`),
	} {
		if validateNetwork(data) == nil {
			t.Fatal("unsafe network profile accepted")
		}
	}
}
