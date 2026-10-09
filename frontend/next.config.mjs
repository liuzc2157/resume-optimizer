/** @type {import('next').NextConfig} */
const nextConfig = {
  // Railway 部署采用 Next.js standalone 模式：node 服务直接监听 Railway 注入的 PORT，
  // 与后端部署机制同构（已验证可用），不再依赖 nginx 端口转发配置。
  output: "standalone",
  reactStrictMode: true,
  // 本项目为纯客户端渲染（数据全部走后端 API），无需图片优化
  images: { unoptimized: true },
};

export default nextConfig;
