/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Phase 7A demo: the UI talks to the same-origin /api path; the Next dev
  // server proxies it to the FastAPI backend (uvicorn on 127.0.0.1:8000).
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: "http://127.0.0.1:8000/api/v1/:path*",
      },
    ];
  },
};

export default nextConfig;
