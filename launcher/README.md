# VideoWorkflow launcher

Bu papkada `VideoWorkflow.exe` (Windows) va `VideoWorkflow-linux` (Linux)
uchun manba kod bor. Launcher Docker Compose stekini ishga tushiradi,
servislar tayyor bo'lishini kutadi va brauzerda saytni ochadi.

## Build

```bash
# Go 1.23+ o'rnatilgan bo'lishi kerak
cd launcher
GOOS=windows GOARCH=amd64 go build -trimpath -ldflags="-s -w" \
  -o ../dist/VideoWorkflow.exe .
GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" \
  -o ../dist/VideoWorkflow-linux .
```

Tashqi Go kutubxonalari ishlatilmagan. Windows'da UTF-8 konsol uchun
`console_windows.go`, boshqa platformalarda `console_other.go` ishlaydi.

## Xavfsizlik

Launcher hech qanday login/parolni so'ramaydi, saqlamaydi yoki chatga
chiqarmaydi. OneID sessiyasi sayt UI'sida kiritiladi.
