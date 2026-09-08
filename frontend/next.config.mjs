/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Phase 8B: the Next server proxies /api/v1/* to the FastAPI backend.
  // The target is read from BACKEND_INTERNAL_URL at server start (server-side
  // only - never a NEXT_PUBLIC_* value). Docker Compose sets
  // http://backend:8000; local development without the env var falls back to
  // http://127.0.0.1:8000. The /api/v1/* rewrite behavior is unchanged.
  async rewrites() {
    const backendInternalUrl =
      process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendInternalUrl}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;