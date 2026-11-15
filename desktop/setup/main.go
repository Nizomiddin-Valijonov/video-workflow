//go:build installer

// VideoWorkflow Studio — bitta faylli Windows o'rnatuvchi.
//
// Ushbu exe ichida tayyor portable paket (payload.zip) bor. Uni
// %LOCALAPPDATA%\VideoWorkflow ga ochadi, ish stoli yorlig'ini yaratadi
// va dasturni ishga tushiradi. Docker/Python/Node kerak emas.
package main

import (
	"archive/zip"
	"bytes"
	_ "embed"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

//go:embed payload.zip
var payload []byte

const appName = "VideoWorkflow Studio"

func main() {
	setupConsole()
	fmt.Println("==============================================")
	fmt.Printf("  %s — o'rnatish\n", appName)
	fmt.Println("==============================================")
	fmt.Println()

	local := os.Getenv("LOCALAPPDATA")
	if local == "" {
		fmt.Println("XATOLIK: LOCALAPPDATA topilmadi.")
		pause()
		os.Exit(1)
	}
	target := filepath.Join(local, "VideoWorkflow")
	fmt.Println("Manzil:", target)

	// Avvalgi nusxa ishlayotgan bo'lsa, uni to'xtatamiz va fayllar
	// bo'shashini kutamiz. Aks holda yangilanish yarim yo'lda uziladi.
	existingExe := filepath.Join(target, "VideoWorkflow.exe")
	if fileExists(existingExe) {
		_ = exec.Command(existingExe, "--stop", "--no-pause").Run()
		if !waitUnlocked(existingExe, 20*time.Second) {
			fmt.Println("XATOLIK: eski nusxa hali ham ishlayapti.")
			fmt.Println("Dasturni qo'lda yoping (oyna va tray), so'ng o'rnatuvchini qayta ishga tushiring.")
			pause()
			os.Exit(1)
		}
	}
	if err := os.MkdirAll(target, 0755); err != nil {
		fmt.Println("XATOLIK: papka yaratilmadi:", err)
		pause()
		os.Exit(1)
	}

	zr, err := zip.NewReader(bytes.NewReader(payload), int64(len(payload)))
	if err != nil {
		fmt.Println("XATOLIK: paket ochilmadi:", err)
		pause()
		os.Exit(1)
	}

	// Yangi paket statik frontend bilan keladi — eski Node serveri
	// qoldiqlarini tozalaymiz (joy va chalkashlikni kamaytiradi).
	if zipHas(zr, "app/frontend/out/index.html") {
		legacyCleanup(target)
	}

	installed := fileExists(filepath.Join(target, "app", "backend", "app", "main.py"))
	fmt.Println("Fayllar ochilmoqda...")
	count := 0
	for _, f := range zr.File {
		name := strings.TrimPrefix(filepath.ToSlash(f.Name), "vw-win-bundle/")
		name = strings.TrimPrefix(name, "/")
		if name == "" || name == "." {
			continue
		}
		// Zip slip himoyasi.
		clean := filepath.Clean(filepath.FromSlash(name))
		if strings.HasPrefix(clean, "..") || filepath.IsAbs(clean) {
			continue
		}
		dst := filepath.Join(target, clean)

		// Yangilashda foydalanuvchi ma'lumotlari va .env saqlanadi.
		if installed {
			rel := filepath.ToSlash(clean)
			if (strings.HasPrefix(rel, "storage/") || strings.HasPrefix(rel, "data/") || rel == "app/backend/.env") && fileExists(dst) {
				continue
			}
		}
		if f.FileInfo().IsDir() {
			_ = os.MkdirAll(dst, 0755)
			continue
		}
		if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
			fmt.Println("XATOLIK:", err)
			pause()
			os.Exit(1)
		}
		if err := extractFile(f, dst); err != nil {
			fmt.Println("XATOLIK:", dst, err)
			pause()
			os.Exit(1)
		}
		count++
	}
	fmt.Printf("Tayyor: %d ta fayl o'rnatildi.\n", count)

	createShortcuts(target)
	fmt.Println()
	fmt.Println("Dastur ishga tushirilmoqda...")
	cmd := exec.Command(existingExe)
	cmd.Dir = target
	if err := cmd.Start(); err != nil {
		fmt.Println("XATOLIK: ishga tushirib bo'lmadi:", err)
		fmt.Println("Qo'lda ishga tushiring:", existingExe)
	} else {
		fmt.Println("Dastur oynasi ochiladi.")
	}
	fmt.Println()
	fmt.Println("O'chirish: VideoWorkflow.exe --stop")
	fmt.Println("Loglar:   VideoWorkflow.exe --logs")
	pause()
}

func extractFile(f *zip.File, dst string) error {
	rc, err := f.Open()
	if err != nil {
		return err
	}
	defer rc.Close()

	var out *os.File
	for attempt := 0; attempt < 20; attempt++ {
		out, err = os.OpenFile(dst, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0644)
		if err == nil {
			break
		}
		// Antivirus yoki hali yopilmagan jarayon faylni ushlab
		// turgan bo'lishi mumkin — qisqa kutib qayta urinamiz.
		time.Sleep(250 * time.Millisecond)
	}
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, rc)
	return err
}

// waitUnlocked faylni yozish uchun ochish mumkin bo'lishini kutadi.
// Ishlayotgan .exe faylini Windows yozish uchun ochishga ruxsat bermaydi.
func waitUnlocked(path string, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for {
		f, err := os.OpenFile(path, os.O_RDWR, 0)
		if err == nil {
			_ = f.Close()
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(300 * time.Millisecond)
	}
}

func zipHas(zr *zip.Reader, wanted string) bool {
	for _, f := range zr.File {
		name := strings.TrimPrefix(filepath.ToSlash(f.Name), "vw-win-bundle/")
		if strings.TrimPrefix(name, "/") == wanted {
			return true
		}
	}
	return false
}

// legacyCleanup eski Node.js asosidagi paket qoldiqlarini o'chiradi.
func legacyCleanup(target string) {
	for _, p := range []string{
		filepath.Join(target, "runtime", "node"),
		filepath.Join(target, "app", "frontend", ".next"),
		filepath.Join(target, "app", "frontend", "node_modules"),
	} {
		_ = os.RemoveAll(p)
	}
	for _, name := range []string{
		"server.js", "package.json", "package-lock.json",
		"next.config.js", "next.config.mjs",
	} {
		_ = os.Remove(filepath.Join(target, "app", "frontend", name))
	}
}

func createShortcuts(target string) {
	exe := filepath.Join(target, "VideoWorkflow.exe")
	exe = strings.ReplaceAll(exe, "'", "''")
	script := fmt.Sprintf(`$ws = New-Object -ComObject WScript.Shell; `+
		`$s = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'VideoWorkflow.lnk')); `+
		`$s.TargetPath = '%s'; $s.WorkingDirectory = '%s'; $s.Description = 'VideoWorkflow Studio'; $s.Save(); `+
		`$p = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'; `+
		`$s2 = $ws.CreateShortcut((Join-Path $p 'VideoWorkflow.lnk')); `+
		`$s2.TargetPath = '%s'; $s2.WorkingDirectory = '%s'; $s2.Description = 'VideoWorkflow Studio'; $s2.Save()`,
		exe, strings.ReplaceAll(target, "'", "''"), exe, strings.ReplaceAll(target, "'", "''"))
	_ = exec.Command("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script).Run()
}

func fileExists(path string) bool {
	st, err := os.Stat(path)
	return err == nil && !st.IsDir()
}

func pause() {
	fmt.Println()
	fmt.Print("Yopish uchun Enter bosing...")
	var b [1]byte
	_, _ = os.Stdin.Read(b[:])
}
