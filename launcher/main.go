// VideoWorkflow Studio launcher.
//
// Bu dastur loyihadagi Docker Compose stekini ishga tushiradi, servislar
// tayyor bo'lishini kutadi va brauzerda saytni ochadi. Docker Desktop
// (Windows) yoki Docker Engine (Linux) o'rnatilgan bo'lishi kerak.
package main

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const (
	appName     = "VideoWorkflow Studio"
	frontendURL = "http://localhost:3000"
	apiDocsURL  = "http://localhost:8000/docs"
)

type options struct {
	path      string
	build     bool
	restart   bool
	stop      bool
	logs      bool
	status    bool
	noBrowser bool
	noPause   bool
	help      bool
}

var projectRoot string
var dockerBin string

func main() {
	setupConsole()
	opts := parseArgs(os.Args[1:])
	if opts.help {
		printHelp()
		return
	}

	root, err := locateProject(opts.path)
	if err != nil {
		fmt.Println()
		fmt.Println("XATOLIK: loyiha papkasi topilmadi.")
		fmt.Println("VideoWorkflow.exe faylini docker-compose.yml joylashgan")
		fmt.Println("papkaga qo'ying yoki quyidagicha ishga tushiring:")
		fmt.Println("  VideoWorkflow.exe --path \"C:\\path\\video-workflow\"")
		pause(opts)
		os.Exit(1)
	}
	projectRoot = root
	if err := os.Chdir(root); err != nil {
		fatal(opts, "Loyiha papkasiga o'tib bo'lmadi: "+err.Error())
	}

	fmt.Println("==============================================")
	fmt.Printf("  %s ishga tushirilmoqda\n", appName)
	fmt.Println("==============================================")
	fmt.Printf("Loyiha: %s\n", root)
	fmt.Println()

	if opts.stop {
		if err := requireCompose(); err != nil {
			fatal(opts, err.Error())
		}
		if err := runCompose("down"); err != nil {
			fatal(opts, "To'xtatishda xato: "+err.Error())
		}
		fmt.Println("Sayt to'xtatildi.")
		pause(opts)
		return
	}
	if opts.logs {
		if err := requireCompose(); err != nil {
			fatal(opts, err.Error())
		}
		_ = runCompose("logs", "-f", "--tail=200")
		return
	}
	if opts.status {
		if err := requireCompose(); err != nil {
			fatal(opts, err.Error())
		}
		_ = runCompose("ps")
		printStatus()
		pause(opts)
		return
	}

	ensureEnv()
	if err := ensureDocker(); err != nil {
		fatal(opts, err.Error())
	}
	if err := requireCompose(); err != nil {
		fatal(opts, err.Error())
	}

	if opts.restart {
		fmt.Println("Eski konteynerlar to'xtatilmoqda...")
		_ = runCompose("down")
	}

	args := []string{"up", "-d"}
	if opts.build {
		args = append(args, "--build")
	}
	fmt.Println("Konteynerlar ishga tushirilmoqda...")
	err = runCompose(args...)
	if err != nil && !opts.build {
		fmt.Println()
		fmt.Println("Birinchi ishga tushirishda image'lar hali yig'ilmagan")
		fmt.Println("bo'lishi mumkin. Qayta yig'ib ko'ramiz (bir necha daqiqa)...")
		err = runCompose("up", "-d", "--build")
	}
	if err != nil {
		fmt.Println()
		fmt.Println("Docker Compose xatosi. Quyidagi loglarni tekshiring:")
		_ = runCompose("ps")
		_ = runCompose("logs", "--tail=60")
		fatal(opts, "Saytni ishga tushirib bo'lmadi.")
	}

	fmt.Println()
	fmt.Println("Servislar tayyor bo'lishi kutilmoqda...")
	if !waitForSite(240 * time.Second) {
		fmt.Println()
		fmt.Println("Diqqat: sayt 4 daqiqada javob bermadi.")
		fmt.Println("Konteynerlar holati:")
		_ = runCompose("ps")
		fmt.Println()
		fmt.Println("Oxirgi loglar:")
		_ = runCompose("logs", "--tail=40")
		fmt.Println()
		fmt.Println("Sayt hali yuklanayotgan bo'lishi mumkin. Brauzerda oching:")
		fmt.Println("  " + frontendURL)
	} else {
		fmt.Println("Sayt tayyor.")
	}

	if !opts.noBrowser {
		openBrowser(frontendURL)
	}
	fmt.Println()
	fmt.Println("  Frontend:    " + frontendURL)
	fmt.Println("  Backend API: http://localhost:8000")
	fmt.Println("  API Docs:    " + apiDocsURL)
	fmt.Println()
	fmt.Println("To'xtatish: VideoWorkflow.exe --stop")
	fmt.Println("Loglar:    VideoWorkflow.exe --logs")
	pause(opts)
}

func parseArgs(args []string) options {
	var o options
	for i := 0; i < len(args); i++ {
		a := args[i]
		switch a {
		case "--path", "-p":
			if i+1 < len(args) {
				i++
				o.path = strings.Trim(args[i], "\"'")
			}
		case "--build":
			o.build = true
		case "--restart":
			o.restart = true
		case "--stop":
			o.stop = true
		case "--logs":
			o.logs = true
		case "--status":
			o.status = true
		case "--no-browser":
			o.noBrowser = true
		case "--no-pause":
			o.noPause = true
		case "--help", "-h", "/?":
			o.help = true
		}
	}
	return o
}

func printHelp() {
	fmt.Println(appName + " launcher")
	fmt.Println()
	fmt.Println("Foydalanish:")
	fmt.Println("  VideoWorkflow.exe                  saytni ishga tushirish")
	fmt.Println("  VideoWorkflow.exe --build          image'larni qayta yig'ish")
	fmt.Println("  VideoWorkflow.exe --restart        to'xtatib, qayta ishga tushirish")
	fmt.Println("  VideoWorkflow.exe --stop           saytni to'xtatish")
	fmt.Println("  VideoWorkflow.exe --logs           loglarni kuzatish")
	fmt.Println("  VideoWorkflow.exe --status         konteynerlar holati")
	fmt.Println("  VideoWorkflow.exe --path DIR       loyiha papkasini ko'rsatish")
	fmt.Println("  VideoWorkflow.exe --no-browser     brauzerni ochmaslik")
	fmt.Println()
	fmt.Println("Talab: Docker Desktop (Windows) yoki Docker Engine (Linux).")
}

func locateProject(explicit string) (string, error) {
	if explicit != "" {
		if isProjectDir(explicit) {
			return filepath.Abs(explicit)
		}
		return "", fmt.Errorf("compose fayli topilmadi: %s", explicit)
	}
	exe, _ := os.Executable()
	exeDir, _ := filepath.Abs(filepath.Dir(exe))
	cwd, _ := os.Getwd()
	home, _ := os.UserHomeDir()

	var candidates []string
	add := func(p string) {
		if p == "" {
			return
		}
		p = filepath.Clean(p)
		for _, c := range candidates {
			if c == p {
				return
			}
		}
		candidates = append(candidates, p)
	}
	add(exeDir)
	add(cwd)
	add(filepath.Dir(exeDir))
	add(filepath.Dir(cwd))
	add(filepath.Join(home, "Desktop", "video-workflow"))
	add(filepath.Join(home, "video-workflow"))
	add(filepath.Join(home, "Desktop", "VideoWorkflow"))
	add(filepath.Join(home, "VideoWorkflow"))
	add(`C:\video-workflow`)

	for _, c := range candidates {
		if isProjectDir(c) {
			return c, nil
		}
	}

	fmt.Println("Loyiha papkasi avtomatik topilmadi.")
	fmt.Print("docker-compose.yml joylashgan papkani kiriting: ")
	line, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	line = strings.TrimSpace(strings.Trim(line, "\"'"))
	if line != "" && isProjectDir(line) {
		return filepath.Abs(line)
	}
	return "", fmt.Errorf("loyiha papkasi topilmadi")
}

func isProjectDir(dir string) bool {
	if dir == "" {
		return false
	}
	for _, name := range []string{"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"} {
		if st, err := os.Stat(filepath.Join(dir, name)); err == nil && !st.IsDir() {
			return true
		}
	}
	return false
}

func ensureEnv() {
	envPath := filepath.Join(projectRoot, "backend", ".env")
	if _, err := os.Stat(envPath); err == nil {
		return
	}
	example := filepath.Join(projectRoot, "backend", ".env.example")
	src, err := os.Open(example)
	if err != nil {
		fmt.Println("Diqqat: backend/.env topilmadi. Uni qo'lda yarating.")
		return
	}
	defer src.Close()
	dst, err := os.Create(envPath)
	if err != nil {
		fmt.Println("Diqqat: backend/.env yaratib bo'lmadi:", err)
		return
	}
	defer dst.Close()
	if _, err := io.Copy(dst, src); err == nil {
		fmt.Println("backend/.env yaratildi (.env.example nusxasi).")
		fmt.Println("AI kalitlarini backend/.env ichida tekshiring.")
	}
}

func ensureDocker() error {
	if dockerOK() {
		return nil
	}
	fmt.Println("Docker hali ishlamayapti. Uni ishga tushirishga harakat qilaman...")
	if runtime.GOOS == "windows" {
		started := tryStartDockerDesktop()
		if !started && findDocker() == "" {
			return fmt.Errorf("Docker Desktop topilmadi. Avval uni o'rnating: https://www.docker.com/products/docker-desktop/")
		}
	}
	deadline := time.Now().Add(180 * time.Second)
	for time.Now().Before(deadline) {
		fmt.Print(".")
		if dockerOK() {
			fmt.Println()
			fmt.Println("Docker tayyor.")
			return nil
		}
		time.Sleep(5 * time.Second)
	}
	fmt.Println()
	return fmt.Errorf("Docker ishga tushmadi. Docker Desktop o'rnatilgan va ishlab turganini tekshiring: https://www.docker.com/products/docker-desktop/")
}

func findDocker() string {
	if dockerBin != "" {
		return dockerBin
	}
	if p, err := exec.LookPath("docker"); err == nil {
		dockerBin = p
		return p
	}
	if runtime.GOOS == "windows" {
		// Docker Desktop ba'zan PATH'ni joriy terminalda yangilamaydi.
		// Standart o'rnatish joylaridan CLI'ni topamiz.
		candidates := []string{
			filepath.Join(os.Getenv("ProgramFiles"), "Docker", "Docker", "resources", "bin", "docker.exe"),
			filepath.Join(os.Getenv("LOCALAPPDATA"), "Docker", "resources", "bin", "docker.exe"),
		}
		for _, p := range candidates {
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				dockerBin = p
				return p
			}
		}
	}
	return ""
}

func dockerOK() bool {
	bin := findDocker()
	if bin == "" {
		return false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, bin, "info").Run() == nil
}

func findLegacyCompose() string {
	if p, err := exec.LookPath("docker-compose"); err == nil {
		return p
	}
	if runtime.GOOS == "windows" {
		candidates := []string{
			filepath.Join(os.Getenv("ProgramFiles"), "Docker", "Docker", "resources", "bin", "docker-compose.exe"),
			filepath.Join(os.Getenv("LOCALAPPDATA"), "Docker", "resources", "bin", "docker-compose.exe"),
		}
		for _, p := range candidates {
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				return p
			}
		}
	}
	return ""
}

func tryStartDockerDesktop() bool {
	paths := []string{
		filepath.Join(os.Getenv("ProgramFiles"), "Docker", "Docker", "Docker Desktop.exe"),
		filepath.Join(os.Getenv("ProgramFiles(x86)"), "Docker", "Docker", "Docker Desktop.exe"),
		filepath.Join(os.Getenv("LOCALAPPDATA"), "Docker", "Docker Desktop.exe"),
	}
	for _, p := range paths {
		if p == "" {
			continue
		}
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			fmt.Println("Docker Desktop ishga tushirilmoqda...")
			if err := exec.Command(p).Start(); err == nil {
				return true
			}
		}
	}
	return false
}

func composeBase() ([]string, error) {
	if bin := findDocker(); bin != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		if exec.CommandContext(ctx, bin, "compose", "version").Run() == nil {
			return []string{bin, "compose"}, nil
		}
	}
	if bin := findLegacyCompose(); bin != "" {
		return []string{bin}, nil
	}
	return nil, fmt.Errorf("docker compose topilmadi")
}

func requireCompose() error {
	_, err := composeBase()
	return err
}

func runCompose(args ...string) error {
	base, err := composeBase()
	if err != nil {
		return err
	}
	cmdArgs := append(append([]string{}, base[1:]...), args...)
	cmd := exec.Command(base[0], cmdArgs...)
	cmd.Dir = projectRoot
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = append(os.Environ(), "COMPOSE_ANSI=never", "DOCKER_CLI_HINTS=false")
	return cmd.Run()
}

func waitForSite(timeout time.Duration) bool {
	client := &http.Client{Timeout: 6 * time.Second}
	deadline := time.Now().Add(timeout)
	seenFrontend := false
	seenAPI := false
	for time.Now().Before(deadline) {
		if !seenFrontend && httpOK(client, frontendURL) {
			seenFrontend = true
		}
		if !seenAPI && httpOK(client, apiDocsURL) {
			seenAPI = true
		}
		if seenFrontend && seenAPI {
			return true
		}
		time.Sleep(3 * time.Second)
	}
	return false
}

func httpOK(client *http.Client, url string) bool {
	resp, err := client.Get(url)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 2048))
	return resp.StatusCode >= 200 && resp.StatusCode < 500
}

func printStatus() {
	fmt.Println()
	fmt.Println("Sayt manzillari:")
	fmt.Println("  " + frontendURL)
	fmt.Println("  http://localhost:8000/docs")
}

func openBrowser(url string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
	case "darwin":
		cmd = exec.Command("open", url)
	default:
		cmd = exec.Command("xdg-open", url)
	}
	if err := cmd.Start(); err != nil {
		fmt.Println("Brauzerni ochib bo'lmadi. Qo'lda oching: " + url)
	}
}

func pause(opts options) {
	if opts.noPause {
		return
	}
	fmt.Println()
	fmt.Print("Yopish uchun Enter bosing...")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
}

func fatal(opts options, msg string) {
	fmt.Println()
	fmt.Println("XATOLIK: " + msg)
	pause(opts)
	os.Exit(1)
}
