/** @type {import('next').NextConfig} */
const publicApiUrl = process.env.NEXT_PUBLIC_API_URL || ""
const backendApiUrl = (process.env.BACKEND_URL
  ? `${process.env.BACKEND_URL.replace(/\/+$/, "")}/api`
  : /^https?:\/\//i.test(publicApiUrl)
    ? publicApiUrl
    : "http://127.0.0.1:8000/api"
).replace(/\/+$/, "")

// Desktop paket uchun statik yig'ilma (`out/`), Docker uchun esa
// Node server (standalone). Bitta kod bazasi ikkala rejimni beradi.
const isStaticExport = (process.env.NEXT_OUTPUT || "").toLowerCase() === "export"

/** @type {import('next').NextConfig} */
const nextConfig = isStaticExport
  ? {
      reactStrictMode: true,
      output: "export",
      // /job?id=... kabi yo'llar papka ko'rinishida (index.html) yoziladi.
      trailingSlash: true,
      images: { unoptimized: true },
      poweredByHeader: false,
    }
  : {
      reactStrictMode: true,
      // Docker/standalone: minimal Node server yig'amiz.
      output: "standalone",
      poweredByHeader: false,
      async rewrites() {
        return [
          {
            // Brauzer /api ga so'rov yuboradi; Next uni backend'ga uzatadi.
            // Shu sabab UI localhost, 127.0.0.1 va boshqa hostlarda bir xil ishlaydi.
            source: "/api/:path*",
            destination: `${backendApiUrl}/:path*`,
          },
        ]
      },
    }

module.exports = nextConfig
