const { defineConfig } = require('@vue/cli-service')

module.exports = defineConfig({
  transpileDependencies: true,
  devServer: {
    // 允许通过局域网 / WSL2 / Docker 等方式访问时，HMR WebSocket 仍可连接
    host: '0.0.0.0',
    allowedHosts: 'all',
    port: 8080,
    client: {
      // 避免在非 localhost 访问时，webpack-dev-server 的 ws 地址推断错误导致 HMR 失败
      // 如果你们通过反向代理/https 访问，可按需改为 wss 或设置为实际域名
      webSocketURL: {
        protocol: 'ws',
        hostname: process.env.WDS_SOCKET_HOST || '192.168.31.219',
        port: process.env.WDS_SOCKET_PORT || 8080,
        pathname: process.env.WDS_SOCKET_PATH || '/ws'
      }
    },
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true
      },
      '/socket.io': {
        target: 'http://localhost:5000',
        ws: true,
        changeOrigin: true
      }
    }
  },
  publicPath: process.env.NODE_ENV === 'production' ? './' : '/'
})

