//go:build !windows

package main

import (
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"syscall"
)

func processAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	p, err := os.FindProcess(pid)
	if err != nil {
		return false
	}
	return p.Signal(syscall.Signal(0)) == nil
}

func desktopUIAvailable() bool { return false }

func notify(title, msg string) {
	fmt.Println(title + ": " + msg)
}

func notifyError(title, msg string) {
	fmt.Fprintln(os.Stderr, title+": "+msg)
}

func hasConsole() bool { return true }

func hideChildWindow(cmd *exec.Cmd) {}

func openFolder(path string) {
	var cmd *exec.Cmd
	if runtime.GOOS == "darwin" {
		cmd = exec.Command("open", path)
	} else {
		cmd = exec.Command("xdg-open", path)
	}
	_ = cmd.Start()
}

func acquireSingleInstance() bool { return true }

func focusExistingWindow() {}

func runDesktopApp(startFn func(stop <-chan struct{}) error, url string) {
	if startFn != nil {
		if err := startFn(nil); err != nil {
			fmt.Println("XATOLIK:", err)
			return
		}
	}
	openBrowser(url)
}
