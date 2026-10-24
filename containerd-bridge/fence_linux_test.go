//go:build linux

package main

import (
	"context"
	"github.com/containerd/containerd/containers"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSessionFenceSerializesAndRejectsOrphans(t *testing.T) {
	path := filepath.Join(t.TempDir(), "operations.lock")
	first := request{OperationLock: path, SessionEpoch: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", BeginRecovery: true}
	held, err := lockSession(context.Background(), first)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	next := first
	next.SessionEpoch = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	if file, err := lockSession(ctx, next); err == nil {
		file.Close()
		t.Fatal("recovery bypassed an active helper")
	}
	held.Close()
	current, err := lockSession(context.Background(), next)
	if err != nil {
		t.Fatal(err)
	}
	current.Close()
	first.BeginRecovery = false
	if stale, err := lockSession(context.Background(), first); err == nil {
		stale.Close()
		t.Fatal("stale orphan crossed session fence")
	}
	next.BeginRecovery = false
	current, err = lockSession(context.Background(), next)
	if err != nil {
		t.Fatal(err)
	}
	current.Close()
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if unsafe, err := lockSession(context.Background(), next); err == nil {
		unsafe.Close()
		t.Fatal("accepted public fence file")
	}
}

func TestContainerSnapshotOwnership(t *testing.T) {
	r := request{ID: "europa-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", OwnerID: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", NotebookID: "A", Generation: 1}
	info := containers.Container{ID: r.ID, SnapshotKey: r.ID + "-rootfs", Snapshotter: "overlayfs", Labels: ownershipLabels(r)}
	if err := verifyContainer(info, r, true); err != nil {
		t.Fatal(err)
	}
	info.SnapshotKey = "unrelated-snapshot"
	if err := verifyContainer(info, r, false); err == nil {
		t.Fatal("accepted unrelated snapshot for destruction")
	}
}
