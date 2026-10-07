/** @type {import('next').NextConfig} */
const nextConfig = {
  // CloudBase「静态网站托管」需要 Next.js 产出 frontend/out 静态文件。
  // 本项目前端为纯客户端渲染（数据全部走后端 API），适合静态导出。
  output: "export",
  reactStrictMode: true,
  // 静态导出时关闭需要服务端的能力（本项目未用到，显式关闭更稳妥）
  images: { unoptimized: true },
};

export default nextConfig;
