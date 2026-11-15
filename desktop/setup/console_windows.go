//go:build windows && installer

package main

import "syscall"

func setupConsole() {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	setConsoleOutputCP := kernel32.NewProc("SetConsoleOutputCP")
	setConsoleCP := kernel32.NewProc("SetConsoleCP")
	_, _, _ = setConsoleOutputCP.Call(65001)
	_, _, _ = setConsoleCP.Call(65001)
}
