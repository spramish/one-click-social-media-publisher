/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Allow the Docker dev server to be reached from the host.
  output: "standalone",
};

export default nextConfig;
