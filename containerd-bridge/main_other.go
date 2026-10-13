//go:build !linux

package main

import "fmt"

func main() { fmt.Println(`{"error":"Linux host required"}`) }
