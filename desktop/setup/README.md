# Windows installer source

`setup/main.go` builds `VideoWorkflow-Setup.exe`: it embeds a portable ZIP
payload (`payload.zip`) and extracts it to `%LOCALAPPDATA%\VideoWorkflow`.

Build:

```bash
# 1) Portable ZIP tayyorlang (ichida vw-win-bundle/ papkasi bo'lsin)
#    masalan: dist/VideoWorkflow-Windows-x64-NoDocker.zip
cp /path/to/VideoWorkflow-Windows-x64-NoDocker.zip payload.zip

# 2) Windows installer
GOOS=windows GOARCH=amd64 go build -tags installer -ldflags "-s -w" -o VideoWorkflow-Setup.exe ./setup
```

`payload.zip` juda katta bo'lgani uchun repo ichida saqlanmaydi.
`dist/VideoWorkflow-Setup.exe` tayyor nusxasi bilan birga beriladi.
