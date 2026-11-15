//go:build windows

package main

import "syscall"

// setupConsole Windows terminalida UTF-8 (o'zbekcha va kirillcha) matnlarni
// to'g'ri ko'rsatish uchun kod sahifasini o'zgartiradi.
func setupConsole() {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	setConsoleOutputCP := kernel32.NewProc("SetConsoleOutputCP")
	setConsoleCP := kernel32.NewProc("SetConsoleCP")
	_, _, _ = setConsoleOutputCP.Call(65001)
	_, _, _ = setConsoleCP.Call(65001)
}
