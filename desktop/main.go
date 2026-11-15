// VideoWorkflow Studio — Docker'siz desktop launcher.
//
// Windows'da dastur native oynada (WebView2) ochiladi; brauzer ishlatilmaydi.
// Frontend statik yig'ilma (app/frontend/out) bo'lsa, uni backend o'zi
// xizmat qiladi — Node.js jarayoni kerak emas. Eski paketlarda (out yo'q)
// Next.js serveri ishga tushiriladi va brauzer ochiladi.
package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const (
	appName       = "VideoWorkflow Studio"
	frontendURL   = "http://localhost:3000"
	backendURL    = "http://127.0.0.1:8000"
	backendHealth = backendURL + "/health"
	backendPort   = "8000"
	frontendPort  = "3000"
)

var errStartCanceled = errors.New("ishga tushirish bekor qilindi")

type options struct {
	path      string
	stop      bool
	status    bool
	logs      bool
	restart   bool
	noBrowser bool
	browser   bool
	noPause   bool
	help      bool
}

var (
	root        string
	pythonExe   string
	nodeExe     string
	chromeExe   string
	backendDir  string
	frontendDir string
	storageDir  string
	dataDir     string
	useStatic   bool
	siteURL     string
	// quiet CLI rejimida (--no-pause) dialog ko'rsatmaymiz: GUI exe'da
	// modal MessageBox jarayonni osilib qoldiradi va .exe faylini
	// bloklaydi (o'rnatuvchi yangilashi shu sababli kutib qoladi).
	quiet bool
	// keepServicesOnExit brauzer-fallback yo'lida xizmatlarni bu
	// jarayon yopilganda o'chirib yubormaslik uchun.
	keepServicesOnExit bool
)

func main() {
	setupConsole()
	opts := parseArgs(os.Args[1:])
	quiet = opts.noPause
	if opts.help {
		printHelp()
		message(appName+" — yordam", helpText())
		return
	}

	var err error
	root, err = locateRoot(opts.path)
	if err != nil {
		fatal(opts, "VideoWorkflow papkasi topilmadi.\n\n"+
			"VideoWorkflow.exe faylini paket papkasida ishga tushiring.\n"+
			"Yoki: VideoWorkflow.exe --path \"C:\\...\\VideoWorkflow\"")
		return
	}
	backendDir = filepath.Join(root, "app", "backend")
	frontendDir = filepath.Join(root, "app", "frontend")
	storageDir = filepath.Join(root, "storage")
	dataDir = filepath.Join(root, "data")

	// Yangi paketlarda frontend statik yig'ilma bo'ladi va backend uni
	// o'zi xizmat qiladi. Eski paketlarda Next.js serveri kerak.
	useStatic = fileExists(filepath.Join(frontendDir, "out", "index.html"))
	if useStatic {
		siteURL = backendURL
	} else {
		siteURL = frontendURL
	}

	var runtimeErr error
	if err := resolveRuntime(useStatic); err != nil {
		runtimeErr = err
	}

	if opts.stop {
		stopEverything()
		message(appName, "Dastur to'xtatildi.")
		pause(opts)
		return
	}
	if opts.status {
		printStatus()
		message(appName+" — holat", statusText())
		pause(opts)
		return
	}
	if opts.logs {
		showLogs()
		logDir := filepath.Join(dataDir, "logs")
		openFolder(logDir)
		message(appName+" — jurnallar", "Log papkasi ochildi:\n"+logDir)
		pause(opts)
		return
	}

	if runtimeErr != nil {
		fatal(opts, runtimeErr.Error())
		return
	}

	if err := os.MkdirAll(dataDir, 0755); err != nil {
		fatal(opts, "data papkasini yaratib bo'lmadi: "+err.Error())
		return
	}
	if err := os.MkdirAll(filepath.Join(dataDir, "logs"), 0755); err != nil {
		fatal(opts, "log papkasini yaratib bo'lmadi: "+err.Error())
		return
	}

	// Windows'da standart rejim — native desktop oyna.
	// --browser bilan eski brauzer rejimini majburlash mumkin.
	gui := runtime.GOOS == "windows" && !opts.browser && !opts.noBrowser

	// --restart avval ishlab turgan oynani (va uning xizmatlarini)
	// to'xtatadi, keyin yangi nusxani ishga tushiradi. Bu blok mutex
	// tekshiruvidan oldin bajarilishi shart.
	if opts.restart {
		stopEverything()
		time.Sleep(1500 * time.Millisecond)
	}

	if gui {
		opts.noPause = true
		if !acquireSingleInstance() {
			focusExistingWindow()
			return
		}
		// --stop/--restart boshqa nusxadan bu jarayonni to'xtata olishi
		// uchun launcher PID'ini yozamiz.
		writePID("desktop", os.Getpid())
		defer os.Remove(pidPath("desktop"))
	}

	// Agar ilova allaqachon ishlayotgan bo'lsa, qayta ishga tushirmaymiz.
	if healthOK() && (useStatic || httpOK(frontendURL)) {
		fmt.Println("Ilova allaqachon ishlayapti.")
		if gui {
			runDesktopApp(nil, siteURL)
			return
		}
		if !opts.noBrowser {
			openBrowser(siteURL)
		}
		printAddresses()
		pause(opts)
		return
	}

	if err := ensureEnvFile(); err != nil {
		fmt.Println("Diqqat: .env yaratishda xato:", err)
	}

	fmt.Println("==============================================")
	fmt.Printf("  %s ishga tushirilmoqda\n", appName)
	fmt.Println("==============================================")
	fmt.Println("Papka:", root)
	fmt.Println("Docker talab qilinmaydi.")
	fmt.Println()

	if gui {
		runDesktopApp(func(stop <-chan struct{}) error {
			return startServices(stop)
		}, siteURL)
		if !keepServicesOnExit {
			stopServices()
		}
		return
	}

	if err := startServices(nil); err != nil {
		fatal(opts, err.Error())
		return
	}
	if !opts.noBrowser {
		openBrowser(siteURL)
	}
	fmt.Println()
	fmt.Println("Ilova tayyor.")
	printAddresses()
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
		case "--stop":
			o.stop = true
		case "--status":
			o.status = true
		case "--logs":
			o.logs = true
		case "--restart":
			o.restart = true
		case "--no-browser":
			o.noBrowser = true
		case "--browser":
			o.browser = true
		case "--no-pause":
			o.noPause = true
		case "--help", "-h", "/?":
			o.help = true
		}
	}
	return o
}

func helpText() string {
	return appName + " — Docker'siz desktop ilova\n\n" +
		"Foydalanish:\n" +
		"  VideoWorkflow.exe                  dasturni ochish\n" +
		"  VideoWorkflow.exe --restart        qayta ishga tushirish\n" +
		"  VideoWorkflow.exe --stop           to'xtatish\n" +
		"  VideoWorkflow.exe --status         holatni tekshirish\n" +
		"  VideoWorkflow.exe --logs           loglarni ko'rsatish\n" +
		"  VideoWorkflow.exe --path DIR       paket papkasini ko'rsatish\n" +
		"  VideoWorkflow.exe --browser        brauzerda ochish (eski rejim)\n" +
		"  VideoWorkflow.exe --no-browser     oyna ochmaslik\n" +
		"  VideoWorkflow.exe --no-pause       konsolni kutmaslik\n\n" +
		"Docker, Python yoki Node o'rnatish shart emas.\n" +
		"Barcha runtime'lar paket ichida."
}

func printHelp() {
	fmt.Println(helpText())
}

func locateRoot(explicit string) (string, error) {
	if explicit != "" {
		abs, err := filepath.Abs(explicit)
		if err == nil && isBundle(abs) {
			return abs, nil
		}
		return "", fmt.Errorf("paket topilmadi: %s", explicit)
	}
	exe, _ := os.Executable()
	exeDir, _ := filepath.Abs(filepath.Dir(exe))
	cwd, _ := os.Getwd()
	candidates := []string{exeDir, cwd, filepath.Dir(exeDir)}
	for _, c := range candidates {
		if isBundle(c) {
			return c, nil
		}
	}
	return "", fmt.Errorf("paket topilmadi")
}

func isBundle(dir string) bool {
	if dir == "" {
		return false
	}
	mainPy := filepath.Join(dir, "app", "backend", "app", "main.py")
	serverJS := filepath.Join(dir, "app", "frontend", "server.js")
	staticHTML := filepath.Join(dir, "app", "frontend", "out", "index.html")
	if !fileExists(mainPy) {
		return false
	}
	return fileExists(serverJS) || fileExists(staticHTML)
}

func resolveRuntime(static bool) error {
	// Windows paketi.
	py := filepath.Join(root, "runtime", "python", "python.exe")
	if runtime.GOOS != "windows" {
		py = filepath.Join(root, "runtime", "python", "bin", "python3")
		if !fileExists(py) {
			py = "python3"
		}
	}
	if runtime.GOOS == "windows" && !fileExists(py) {
		return fmt.Errorf("Python runtime topilmadi: %s", py)
	}
	pythonExe = py

	if !static {
		node := filepath.Join(root, "runtime", "node", "node.exe")
		if runtime.GOOS != "windows" {
			node = filepath.Join(root, "runtime", "node", "bin", "node")
			if !fileExists(node) {
				node = "node"
			}
		}
		if runtime.GOOS == "windows" && !fileExists(node) {
			return fmt.Errorf("Node.js runtime topilmadi: %s", node)
		}
		nodeExe = node
		if !fileExists(filepath.Join(root, "app", "frontend", "server.js")) {
			return fmt.Errorf("frontend server.js topilmadi")
		}
	}

	chromeCandidates := []string{
		filepath.Join(root, "browsers", "chrome-win64", "chrome.exe"),
		filepath.Join(root, "browsers", "chrome-linux64", "chrome"),
		filepath.Join(root, "browsers", "chromium", "chrome-win", "chrome.exe"),
		filepath.Join(root, "browsers", "chromium", "chrome-linux", "chrome"),
	}
	for _, p := range chromeCandidates {
		if fileExists(p) {
			chromeExe = p
			break
		}
	}
	if !fileExists(filepath.Join(root, "app", "backend", "app", "main.py")) {
		return fmt.Errorf("backend papkasi topilmadi")
	}
	return nil
}

func ensureEnvFile() error {
	envPath := filepath.Join(backendDir, ".env")
	if !fileExists(envPath) {
		example := filepath.Join(backendDir, ".env.local.example")
		if fileExists(example) {
			src, err := os.ReadFile(example)
			if err != nil {
				return err
			}
			text := string(src)
			if strings.Contains(text, "SECRET_KEY=local-change-me") {
				text = strings.ReplaceAll(text, "SECRET_KEY=local-change-me", "SECRET_KEY="+randomHex(32))
			}
			return os.WriteFile(envPath, []byte(text), 0600)
		}
		return os.WriteFile(envPath, []byte("SECRET_KEY="+randomHex(32)+"\n"), 0600)
	}
	// Eski nusxada placeholder qolgan bo'lsa, uni almashtiramiz.
	raw, err := os.ReadFile(envPath)
	if err == nil && strings.Contains(string(raw), "SECRET_KEY=local-change-me") {
		text := strings.ReplaceAll(string(raw), "SECRET_KEY=local-change-me", "SECRET_KEY="+randomHex(32))
		return os.WriteFile(envPath, []byte(text), 0600)
	}
	return nil
}

func randomHex(n int) string {
	buf := make([]byte, n)
	if _, err := rand.Read(buf); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(buf)
}

func sqliteURL(path string) string {
	p := filepath.ToSlash(path)
	return "sqlite+aiosqlite:///" + p
}

// withEnv mavjud muhit o'zgaruvchilarini saqlab, faqat kerakli
// kalitlarni yangilaydi. Duplicate PATH/DATABASE_URL yozuvlari
// Windows'da kutilmagan natija bermasligi uchun ishlatiladi.
func withEnv(updates map[string]string) []string {
	norm := func(s string) string {
		if runtime.GOOS == "windows" {
			return strings.ToUpper(s)
		}
		return s
	}
	updateKeys := make(map[string]struct{}, len(updates))
	for k := range updates {
		updateKeys[norm(k)] = struct{}{}
	}
	out := make([]string, 0, len(os.Environ())+len(updates))
	for _, item := range os.Environ() {
		key := item
		if i := strings.IndexByte(item, '='); i >= 0 {
			key = item[:i]
		}
		if _, replace := updateKeys[norm(key)]; replace {
			continue
		}
		out = append(out, item)
	}
	for k, v := range updates {
		out = append(out, k+"="+v)
	}
	return out
}

func backendEnvironment() []string {
	pathParts := []string{
		filepath.Dir(pythonExe),
		filepath.Join(filepath.Dir(pythonExe), "Scripts"),
	}
	if nodeExe != "" {
		pathParts = append(pathParts, filepath.Dir(nodeExe))
	}
	updates := map[string]string{
		"LOCAL_MODE":               "true",
		"ENVIRONMENT":              "development",
		"DATABASE_URL":             sqliteURL(filepath.Join(dataDir, "app.db")),
		"STORAGE_DIR":              storageDir,
		"PLAYWRIGHT_BROWSERS_PATH": filepath.Join(root, "browsers"),
		"PYTHONPATH":               backendDir,
		"PYTHONDONTWRITEBYTECODE":  "1",
		"PYTHONUTF8":               "1",
		"PYTHONIOENCODING":         "utf-8",
		"CORS_ORIGINS":             `["http://localhost:8000","http://127.0.0.1:8000","http://localhost:3000","http://127.0.0.1:3000"]`,
		"TRUSTED_HOSTS":            `["localhost","127.0.0.1","*"]`,
		"BROWSER_HEADLESS":         "true",
	}
	if useStatic {
		updates["FRONTEND_DIST"] = filepath.Join(frontendDir, "out")
	}
	if chromeExe != "" {
		updates["BROWSER_EXECUTABLE"] = chromeExe
		updates["BROWSER_CHANNEL"] = ""
	} else {
		updates["BROWSER_CHANNEL"] = "msedge"
	}
	updates["PATH"] = strings.Join(append(pathParts, os.Getenv("PATH")), string(os.PathListSeparator))

	// Embeddable Python `._pth` yoqilgan bo'lsa PYTHONPATH e'tiborsiz
	// qolishi mumkin. Shuning uchun backend yo'lini .pth fayl orqali
	// qo'shamiz (Windows'da ishlaydi, Linux'da zarar qilmaydi).
	_ = writeAppPth()
	return withEnv(updates)
}

func writeAppPth() error {
	if !strings.Contains(strings.ToLower(pythonExe), "runtime") {
		return nil
	}
	pyDir := filepath.Dir(pythonExe)
	site := filepath.Join(pyDir, "Lib", "site-packages")
	if runtime.GOOS != "windows" {
		site = filepath.Join(pyDir, "..", "lib", "python3.12", "site-packages")
	}
	if err := os.MkdirAll(site, 0755); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(site, "videoworkflow_app.pth"), []byte(backendDir+"\n"), 0644); err != nil {
		return err
	}
	customize := fmt.Sprintf("# -*- coding: utf-8 -*-\nimport sys\nsys.path.insert(0, %q)\n", backendDir)
	return os.WriteFile(filepath.Join(site, "sitecustomize.py"), []byte(customize), 0644)
}

func frontendEnvironment() []string {
	return withEnv(map[string]string{
		"NODE_ENV":                "production",
		"PORT":                    frontendPort,
		"HOSTNAME":                "127.0.0.1",
		"NEXT_TELEMETRY_DISABLED": "1",
		"PATH":                    filepath.Dir(nodeExe) + string(os.PathListSeparator) + os.Getenv("PATH"),
	})
}

func startServices(stop <-chan struct{}) error {
	logDir := filepath.Join(dataDir, "logs")

	backendRunning := healthOK()
	frontendRunning := !useStatic && httpOK(frontendURL)
	if backendRunning {
		fmt.Println("Backend allaqachon ishlayapti — qayta ishga tushirilmaydi.")
	} else if pid := readPID("backend"); pid > 0 && processAlive(pid) {
		// PID tirik, lekin health javob bermayapti: osilib qolgan
		// jarayonni tozalaymiz, aks holda port band bo'lib qoladi.
		killPID(pid)
		_ = os.Remove(pidPath("backend"))
	}
	if !useStatic && frontendRunning {
		fmt.Println("Frontend allaqachon ishlayapti — qayta ishga tushirilmaydi.")
	} else if !useStatic {
		if pid := readPID("frontend"); pid > 0 && processAlive(pid) && !httpOK(frontendURL) {
			killPID(pid)
			_ = os.Remove(pidPath("frontend"))
		}
	}
	if backendRunning && (useStatic || frontendRunning) {
		return nil
	}

	backendLog, err := os.OpenFile(filepath.Join(logDir, "backend.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer backendLog.Close()

	if !backendRunning {
		fmt.Println("Backend ishga tushirilmoqda...")
		backend := exec.Command(pythonExe, "-m", "uvicorn", "app.main:app",
			"--host", "127.0.0.1", "--port", backendPort, "--log-level", "info")
		backend.Dir = backendDir
		backend.Env = backendEnvironment()
		backend.Stdout = backendLog
		backend.Stderr = backendLog
		hideChildWindow(backend)
		if err := backend.Start(); err != nil {
			return fmt.Errorf("backend ishga tushmadi: %w", err)
		}
		writePID("backend", backend.Process.Pid)
	}

	if !useStatic && !frontendRunning {
		frontendLog, err := os.OpenFile(filepath.Join(logDir, "frontend.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
		if err != nil {
			stopServices()
			return err
		}
		defer frontendLog.Close()

		fmt.Println("Frontend ishga tushirilmoqda...")
		frontend := exec.Command(nodeExe, "server.js")
		frontend.Dir = frontendDir
		frontend.Env = frontendEnvironment()
		frontend.Stdout = frontendLog
		frontend.Stderr = frontendLog
		hideChildWindow(frontend)
		if err := frontend.Start(); err != nil {
			stopServices()
			return fmt.Errorf("frontend ishga tushmadi: %w", err)
		}
		writePID("frontend", frontend.Process.Pid)
	}

	fmt.Println("Ilova tayyor bo'lishi kutilmoqda (90 soniyagacha)...")
	if waitForSite(90*time.Second, stop) {
		return nil
	}
	if stop != nil {
		select {
		case <-stop:
			return errStartCanceled
		default:
		}
	}

	fmt.Println()
	fmt.Println("Ilova 90 soniyada javob bermadi.")
	printTail(filepath.Join(logDir, "backend.log"), 25)
	if !useStatic {
		printTail(filepath.Join(logDir, "frontend.log"), 25)
	}
	return fmt.Errorf("ilova tayyor bo'lmadi (loglarni tekshiring)")
}

func waitForSite(timeout time.Duration, stop <-chan struct{}) bool {
	deadline := time.Now().Add(timeout)
	seenBackend, seenFrontend := false, false
	for time.Now().Before(deadline) {
		if stop != nil {
			select {
			case <-stop:
				return false
			default:
			}
		}
		if !seenBackend && healthOK() {
			seenBackend = true
			fmt.Print(" backend✓")
		}
		if useStatic {
			seenFrontend = seenBackend
		} else if !seenFrontend && httpOK(frontendURL) {
			seenFrontend = true
			fmt.Print(" frontend✓")
		}
		if seenBackend && seenFrontend {
			fmt.Println()
			return true
		}
		time.Sleep(1 * time.Second)
	}
	fmt.Println()
	return false
}

func httpOK(url string) bool {
	client := &http.Client{Timeout: 4 * time.Second}
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, url, nil)
	if err != nil {
		return false
	}
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 2048))
	return resp.StatusCode >= 200 && resp.StatusCode < 400
}

// healthOK faqat bizning backend javob berayotganiga ishonch hosil qiladi:
// 200 va `{"ok":true}` tanasi. Begona xizmat portni egallagan bo'lsa,
// uni "ilova allaqachon ishlayapti" deb qabul qilmaymiz.
func healthOK() bool {
	client := &http.Client{Timeout: 4 * time.Second}
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, backendHealth, nil)
	if err != nil {
		return false
	}
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return false
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 4096))
	return err == nil && strings.Contains(string(body), `"ok":true`)
}

func pidPath(name string) string {
	return filepath.Join(dataDir, name+".pid")
}

func writePID(name string, pid int) {
	_ = os.WriteFile(pidPath(name), []byte(strconv.Itoa(pid)), 0644)
}

func readPID(name string) int {
	raw, err := os.ReadFile(pidPath(name))
	if err != nil {
		return 0
	}
	pid, _ := strconv.Atoi(strings.TrimSpace(string(raw)))
	return pid
}

func stopEverything() {
	// Avval launcher oynasini yopamiz: u backend/frontend jarayonlarining
	// ota-onasi, shuning uchun /T bilan butun daraxt to'xtaydi.
	if pid := readPID("desktop"); pid > 0 && pid != os.Getpid() {
		killPID(pid)
		_ = os.Remove(pidPath("desktop"))
		time.Sleep(300 * time.Millisecond)
	}
	stopServices()
}

func stopServices() {
	fmt.Println("Ilova to'xtatilmoqda...")
	for _, name := range []string{"frontend", "backend"} {
		if pid := readPID(name); pid > 0 {
			killPID(pid)
			_ = os.Remove(pidPath(name))
			fmt.Printf("  %s (PID %d) to'xtatildi.\n", name, pid)
		}
	}
}

func killPID(pid int) {
	if runtime.GOOS == "windows" {
		_ = exec.Command("taskkill", "/PID", strconv.Itoa(pid), "/T", "/F").Run()
		return
	}
	proc, err := os.FindProcess(pid)
	if err == nil {
		_ = proc.Kill()
	}
}

func printStatus() {
	fmt.Println(appName)
	fmt.Println("  Papka:", root)
	fmt.Printf("  Dastur:   %s\n", statusTextFor(launcherRunning()))
	fmt.Printf("  Backend:  %s  (%s)\n", backendHealth, statusTextFor(healthOK()))
	if !useStatic {
		fmt.Printf("  Frontend: %s  (%s)\n", frontendURL, statusTextFor(httpOK(frontendURL)))
	}
	for _, name := range []string{"desktop", "backend", "frontend"} {
		if pid := readPID(name); pid > 0 {
			fmt.Printf("  %s PID: %d\n", name, pid)
		}
	}
	fmt.Println()
	fmt.Println("Manzil: " + siteURL)
}

func statusText() string {
	lines := []string{
		appName,
		"  Papka: " + root,
		"  Dastur: " + statusTextFor(launcherRunning()),
		"  Backend: " + statusTextFor(healthOK()),
	}
	if !useStatic {
		lines = append(lines, "  Frontend: "+statusTextFor(httpOK(frontendURL)))
	}
	return strings.Join(lines, "\n")
}

func launcherRunning() bool {
	pid := readPID("desktop")
	return pid > 0 && pid != os.Getpid() && processAlive(pid)
}

func statusTextFor(ok bool) string {
	if ok {
		return "ishlayapti"
	}
	return "ishlamayapti"
}

func printAddresses() {
	fmt.Println()
	if useStatic {
		fmt.Println("  Ilova:       " + backendURL)
	} else {
		fmt.Println("  Ilova:       " + frontendURL)
	}
	fmt.Println("  API:         " + backendURL)
	fmt.Println("  API Docs:    " + backendURL + "/docs")
}

func showLogs() {
	logDir := filepath.Join(dataDir, "logs")
	fmt.Println("Backend log:", filepath.Join(logDir, "backend.log"))
	printTail(filepath.Join(logDir, "backend.log"), 80)
	if !useStatic {
		fmt.Println()
		fmt.Println("Frontend log:", filepath.Join(logDir, "frontend.log"))
		printTail(filepath.Join(logDir, "frontend.log"), 80)
	}
}

func printTail(path string, lines int) {
	f, err := os.Open(path)
	if err != nil {
		fmt.Println("  (log topilmadi)")
		return
	}
	defer f.Close()
	const maxBytes = 256 * 1024
	stat, err := f.Stat()
	if err == nil && stat.Size() > maxBytes {
		_, _ = f.Seek(-maxBytes, io.SeekEnd)
	}
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 4096), 1024*1024)
	var all []string
	for scanner.Scan() {
		all = append(all, scanner.Text())
	}
	start := len(all) - lines
	if start < 0 {
		start = 0
	}
	for _, line := range all[start:] {
		fmt.Println("  " + line)
	}
}

func logTailText(lines int) string {
	path := filepath.Join(dataDir, "logs", "backend.log")
	f, err := os.Open(path)
	if err != nil {
		return "Log topilmadi: " + path
	}
	defer f.Close()
	const maxBytes = 128 * 1024
	if stat, err := f.Stat(); err == nil && stat.Size() > maxBytes {
		_, _ = f.Seek(-maxBytes, io.SeekEnd)
	}
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 4096), 1024*1024)
	var all []string
	for scanner.Scan() {
		all = append(all, scanner.Text())
	}
	start := len(all) - lines
	if start < 0 {
		start = 0
	}
	return strings.Join(all[start:], "\n")
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

func fileExists(path string) bool {
	st, err := os.Stat(path)
	return err == nil && !st.IsDir()
}

func pause(opts options) {
	if opts.noPause || !hasConsole() {
		return
	}
	fmt.Println()
	fmt.Print("Yopish uchun Enter bosing...")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
}

// message faqat konsoli bo'lmagan (GUI) ishga tushirishda dialog ko'rsatadi.
// --no-pause (skript/avtomatik rejim) bilan dialog ko'rsatilmaydi.
func message(title, msg string) {
	if quiet {
		return
	}
	if desktopUIAvailable() && !hasConsole() {
		notify(title, msg)
	}
}

func fatal(opts options, msg string) {
	fmt.Println()
	fmt.Println("XATOLIK: " + msg)
	if !quiet && desktopUIAvailable() {
		notifyError(appName+" — xatolik", msg)
	}
	pause(opts)
	if opts.noPause {
		os.Exit(1)
	}
}
