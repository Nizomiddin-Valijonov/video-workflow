//go:build windows

package main

import (
	"errors"
	"fmt"
	"html"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"
	"unsafe"

	"github.com/jchv/go-webview2"
	"golang.org/x/sys/windows"
)

const (
	createNoWindow = 0x08000000
	mbIconInfo     = 0x00000040
	mbIconError    = 0x00000010
	swRestore      = 9
)

var instanceMutex windows.Handle

func desktopUIAvailable() bool { return true }

func notify(title, msg string) {
	user32 := syscall.NewLazyDLL("user32.dll")
	messageBox := user32.NewProc("MessageBoxW")
	t, err := syscall.UTF16PtrFromString(title)
	if err != nil {
		return
	}
	m, err := syscall.UTF16PtrFromString(msg)
	if err != nil {
		return
	}
	_, _, _ = messageBox.Call(0,
		uintptr(unsafe.Pointer(m)),
		uintptr(unsafe.Pointer(t)),
		mbIconInfo)
}

func notifyError(title, msg string) {
	user32 := syscall.NewLazyDLL("user32.dll")
	messageBox := user32.NewProc("MessageBoxW")
	t, _ := syscall.UTF16PtrFromString(title)
	m, _ := syscall.UTF16PtrFromString(msg)
	_, _, _ = messageBox.Call(0,
		uintptr(unsafe.Pointer(m)),
		uintptr(unsafe.Pointer(t)),
		mbIconError)
}

func hasConsole() bool {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	getConsoleWindow := kernel32.NewProc("GetConsoleWindow")
	hwnd, _, _ := getConsoleWindow.Call()
	return hwnd != 0
}

func hideChildWindow(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.HideWindow = true
	cmd.SysProcAttr.CreationFlags |= createNoWindow
}

func openFolder(path string) {
	_ = exec.Command("explorer.exe", path).Start()
}

// acquireSingleInstance ikkinchi nusxa ochilishini oldini oladi.
// windows.CreateMutex yordamchisi ERROR_ALREADY_EXISTS'ni yutib yuboradi,
// shuning uchun Win32 chaqiruvini to'g'ridan-to'g'ri ishlatamiz.
func acquireSingleInstance() bool {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	createMutex := kernel32.NewProc("CreateMutexW")
	name, err := syscall.UTF16PtrFromString(`Local\VideoWorkflowStudio`)
	if err != nil {
		return true
	}
	h, _, callErr := createMutex.Call(0, 0, uintptr(unsafe.Pointer(name)))
	if h == 0 {
		return true
	}
	if callErr == windows.ERROR_ALREADY_EXISTS {
		_ = windows.CloseHandle(windows.Handle(h))
		return false
	}
	instanceMutex = windows.Handle(h)
	return true
}

func processAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(h)
	var code uint32
	if err := windows.GetExitCodeProcess(h, &code); err != nil {
		return true
	}
	return code == 259 // STILL_ACTIVE
}

func focusExistingWindow() {
	user32 := syscall.NewLazyDLL("user32.dll")
	findWindow := user32.NewProc("FindWindowW")
	showWindow := user32.NewProc("ShowWindow")
	setForeground := user32.NewProc("SetForegroundWindow")
	title, err := syscall.UTF16PtrFromString(appName)
	if err != nil {
		return
	}
	hwnd, _, _ := findWindow.Call(0, uintptr(unsafe.Pointer(title)))
	if hwnd == 0 {
		notify(appName, "Dastur allaqachon ishlayapti.")
		return
	}
	_, _, _ = showWindow.Call(hwnd, swRestore)
	_, _, _ = setForeground.Call(hwnd)
}

// runDesktopApp native WebView2 oynasini ochadi va uni yopilguncha
// kutadi. startFn nil bo'lsa, xizmatlar allaqachon ishlayapti deb
// hisoblanadi.
func runDesktopApp(startFn func(stop <-chan struct{}) error, url string) {
	dataPath := filepath.Join(dataDir, "webview2")
	_ = os.MkdirAll(dataPath, 0755)

	var w webview2.WebView
	func() {
		defer func() {
			if r := recover(); r != nil {
				w = nil
			}
		}()
		w = webview2.NewWithOptions(webview2.WebViewOptions{
			Debug:     false,
			AutoFocus: true,
			DataPath:  dataPath,
			WindowOptions: webview2.WindowOptions{
				Title:  appName,
				Width:  1440,
				Height: 900,
				Center: true,
			},
		})
	}()

	if w == nil {
		// WebView2 runtime topilmadi — xizmatlarni shu (qisqa umrli)
		// jarayonda emas, alohida brauzer nusxasida ishga tushiramiz.
		// Aks holda brauzer ochilishi bilan xizmatlar to'xtab qoladi.
		notify(appName, "WebView2 runtime topilmadi.\n\n"+
			"Ilova vaqtincha standart brauzerda ochiladi.\n"+
			"Doimiy yechim: Microsoft Edge WebView2 Runtime'ni o'rnating.")
		if exe, err := os.Executable(); err == nil {
			cmd := exec.Command(exe, "--browser", "--no-pause", "--path", root)
			cmd.Dir = root
			hideChildWindow(cmd)
			if err := cmd.Start(); err == nil {
				keepServicesOnExit = true
				return
			}
		}
		if startFn != nil {
			if err := startFn(nil); err != nil {
				notifyError(appName+" — xatolik", err.Error()+"\n\n"+logTailText(20))
				return
			}
			keepServicesOnExit = true
		}
		openBrowser(url)
		return
	}
	defer w.Destroy()

	w.SetSize(1024, 700, webview2.HintMin)
	w.SetHtml(loadingHTML())

	stop := make(chan struct{})
	done := make(chan struct{})
	go func() {
		defer close(done)
		if startFn != nil {
			if err := startFn(stop); err != nil {
				if errors.Is(err, errStartCanceled) {
					return
				}
				w.Dispatch(func() { w.SetHtml(errorHTML(err.Error(), logTailText(30), url)) })
				return
			}
		}
		select {
		case <-stop:
			return
		default:
		}
		w.Dispatch(func() { w.Navigate(url) })
	}()

	w.Run()

	close(stop)
	select {
	case <-done:
	case <-time.After(3 * time.Second):
	}
}

func loadingHTML() string {
	return `<!doctype html>
<html lang="uz"><head><meta charset="utf-8">
<style>
  html,body{height:100%;margin:0}
  body{background:#0b1020;color:#e6ebff;font:15px/1.5 "Segoe UI",system-ui,sans-serif;
       display:flex;align-items:center;justify-content:center;flex-direction:column;gap:22px}
  .spinner{width:42px;height:42px;border-radius:50%;border:3px solid #26304d;
           border-top-color:#5b8cff;animation:spin .9s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  h1{font-size:19px;font-weight:600;margin:0}
  p{color:#8f9bbd;margin:0;font-size:13px}
</style></head>
<body>
  <div class="spinner"></div>
  <h1>VideoWorkflow Studio</h1>
  <p>Ilova ishga tushirilmoqda... birinchi ochilishda biroz kutishingiz mumkin.</p>
</body></html>`
}

func errorHTML(message, logTail, retryURL string) string {
	esc := html.EscapeString(message)
	tail := html.EscapeString(logTail)
	retry := html.EscapeString(retryURL)
	return fmt.Sprintf(`<!doctype html>
<html lang="uz"><head><meta charset="utf-8">
<style>
  html,body{height:100%%;margin:0}
  body{background:#0b1020;color:#e6ebff;font:14px/1.5 "Segoe UI",system-ui,sans-serif;
       padding:36px;box-sizing:border-box}
  h1{font-size:20px;margin:0 0 12px;color:#ff7b7b}
  p{color:#aab4d4;margin:0 0 18px}
  pre{background:#131a30;border:1px solid #232d4d;border-radius:8px;padding:14px;
      overflow:auto;max-height:46vh;font:12px/1.45 Consolas,monospace;color:#c6d0ec;white-space:pre-wrap}
  button{background:#3f6fff;border:0;color:#fff;font:600 14px "Segoe UI",sans-serif;
         padding:10px 20px;border-radius:8px;cursor:pointer;margin-top:18px}
  button:hover{background:#5b84ff}
</style></head>
<body>
  <h1>Ilova ishga tushmadi</h1>
  <p>%s</p>
  <pre>%s</pre>
  <button onclick="location.href='%s'">Qayta urinish</button>
</body></html>`, esc, tail, retry)
}
