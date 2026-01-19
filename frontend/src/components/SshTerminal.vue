<template>
  <div class="ssh-terminal-container">
    <div class="ssh-terminal-header">
      <div class="ssh-status">
        <span class="status-dot" :class="{ connected: connected }"></span>
        <span class="status-text">{{ connected ? '已连接' : '未连接' }}</span>
      </div>
      <a-button 
        @click="disconnect" 
        danger 
        :disabled="!connected"
        class="disconnect-btn"
      >
        <span style="margin-right: 6px">🔌</span>
        断开连接
      </a-button>
    </div>
    <div ref="terminalRef" class="terminal-wrapper"></div>
  </div>
</template>

<script>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { io } from 'socket.io-client'
import { message } from 'ant-design-vue'

export default {
  name: 'SshTerminal',
  props: {
    serverId: {
      type: Number,
      required: true
    },
    autoCommand: {
      type: String,
      default: null
    },
    serviceId: {
      type: Number,
      default: null
    }
  },
  setup(props) {
    const terminalRef = ref(null)
    const terminal = ref(null)
    const fitAddon = ref(null)
    const socket = ref(null)
    const connected = ref(false)
    let resizeObserver = null
    let resizeTimer = null

    // 发送终端尺寸到SSH服务器
    const sendTerminalSize = () => {
      if (socket.value && connected.value && terminal.value && fitAddon.value) {
        try {
          const dimensions = fitAddon.value.proposeDimensions()
          if (dimensions && dimensions.cols > 0 && dimensions.rows > 0) {
            socket.value.emit('ssh_resize', {
              cols: dimensions.cols,
              rows: dimensions.rows
            })
          }
        } catch (error) {
          console.warn('Error sending terminal size:', error)
        }
      }
    }

    const initTerminal = () => {
      if (!terminalRef.value) return

      // 如果已经存在终端实例，先清理
      if (terminal.value) {
        try {
          terminal.value.dispose()
        } catch (error) {
          console.error('Error disposing existing terminal:', error)
        }
      }

      // 创建终端实例
      terminal.value = new Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: 'Consolas, "Courier New", monospace',
        scrollback: 1000, // 设置滚动缓冲区大小
        allowTransparency: false,
        disableStdin: false,
        theme: {
          background: '#1e1e1e',
          foreground: '#d4d4d4',
          cursor: '#aeafad',
          cursorAccent: '#000000',
          selection: '#264f78',
          black: '#000000',
          red: '#cd3131',
          green: '#0dbc79',
          yellow: '#e5e510',
          blue: '#2472c8',
          magenta: '#bc3fbc',
          cyan: '#11a8cd',
          white: '#e5e5e5',
          brightBlack: '#666666',
          brightRed: '#f14c4c',
          brightGreen: '#23d18b',
          brightYellow: '#f5f543',
          brightBlue: '#3b8eea',
          brightMagenta: '#d670d6',
          brightCyan: '#29b8db',
          brightWhite: '#e5e5e5'
        }
      })

      // 添加fit addon用于自动调整大小
      fitAddon.value = new FitAddon()
      terminal.value.loadAddon(fitAddon.value)

      // 打开终端
      terminal.value.open(terminalRef.value)

      // 延迟执行fit，确保DOM完全渲染
      setTimeout(() => {
        if (fitAddon.value) {
          fitAddon.value.fit()
        }
      }, 50)

      // 确保初始状态下滚动到底部
      setTimeout(() => {
        if (terminal.value) {
          terminal.value.scrollToBottom()
        }
      }, 100)

      // 处理终端输入
      terminal.value.onData((data) => {
        if (socket.value && connected.value) {
          socket.value.emit('ssh_input', { input: data })
        }
      })

      // 处理终端大小变化
      if (window.ResizeObserver) {
        resizeObserver = new ResizeObserver(() => {
          if (fitAddon.value && terminal.value) {
            // 使用防抖来避免频繁触发和 ResizeObserver 循环警告
            if (resizeTimer) {
              clearTimeout(resizeTimer)
            }
            resizeTimer = setTimeout(() => {
              try {
                if (fitAddon.value && terminal.value) {
                  fitAddon.value.fit()
                  // 调整大小后发送新的终端尺寸到SSH服务器
                  setTimeout(() => {
                    sendTerminalSize()
                    // 重新滚动到底部
                    if (terminal.value) {
                      terminal.value.scrollToBottom()
                    }
                  }, 50)
                }
              } catch (error) {
                // 忽略 ResizeObserver 循环警告（这是一个已知的浏览器警告，不影响功能）
                if (!error.message || !error.message.includes('ResizeObserver')) {
                  console.error('Error fitting terminal:', error)
                }
              }
            }, 100)
          }
        })
        resizeObserver.observe(terminalRef.value)
      }
    }

    const connect = () => {
      socket.value = io('http://localhost:5000', {
        transports: ['websocket', 'polling']
      })

      socket.value.on('connect', () => {
        if (terminal.value) {
          terminal.value.writeln('正在连接SSH...')
        }
        const connectData = { 
          server_id: props.serverId 
        }
        if (props.autoCommand) {
          connectData.auto_command = props.autoCommand
        }
        if (props.serviceId) {
          connectData.service_id = props.serviceId
        }
        socket.value.emit('ssh_connect', connectData)
      })

      socket.value.on('ssh_connected', (data) => {
        connected.value = true
        if (terminal.value) {
          terminal.value.clear()
          terminal.value.writeln('SSH连接已建立')
          // 连接建立后，等待fitAddon完成，然后发送终端尺寸
          setTimeout(() => {
            if (fitAddon.value && terminal.value) {
              fitAddon.value.fit()
              // 再等待一下确保尺寸计算完成
              setTimeout(() => {
                sendTerminalSize()
                // 滚动到底部
                if (terminal.value) {
                  terminal.value.scrollToBottom()
                }
              }, 100)
            }
          }, 100)
        }
      })

      socket.value.on('ssh_output', (data) => {
        if (terminal.value && data.data) {
          terminal.value.write(data.data)
          // 每次收到输出后自动滚动到底部
          setTimeout(() => {
            if (terminal.value) {
              terminal.value.scrollToBottom()
            }
          }, 10)
        }
      })

      socket.value.on('ssh_error', (data) => {
        message.error('SSH连接错误: ' + data.error)
        if (terminal.value) {
          terminal.value.writeln('\r\n错误: ' + data.error)
          // 错误信息后滚动到底部
          setTimeout(() => {
            if (terminal.value) {
              terminal.value.scrollToBottom()
            }
          }, 10)
        }
      })

      socket.value.on('ssh_disconnected', () => {
        connected.value = false
        if (terminal.value) {
          terminal.value.writeln('\r\nSSH连接已断开')
          // 断开连接后滚动到底部
          setTimeout(() => {
            if (terminal.value) {
              terminal.value.scrollToBottom()
            }
          }, 10)
        }
      })

      socket.value.on('disconnect', () => {
        connected.value = false
      })
    }

    const disconnect = async () => {
      if (!socket.value) {
        connected.value = false
        return
      }
      
      // 如果是服务持久化连接，不发送exit命令，只断开WebSocket
      const isPersistent = !!props.serviceId
      
      if (!isPersistent && connected.value && terminal.value) {
        // 临时连接：发送exit命令优雅地退出SSH会话
        try {
          // 发送exit命令和换行符
          socket.value.emit('ssh_input', { input: 'exit\r\n' })
          // 等待一小段时间让exit命令执行
          await new Promise(resolve => setTimeout(resolve, 300))
        } catch (error) {
          console.warn('Error sending exit command:', error)
        }
      }
      
      // 断开连接
      try {
        socket.value.emit('ssh_disconnect')
        socket.value.disconnect()
      } catch (error) {
        console.warn('Error disconnecting socket:', error)
      }
      socket.value = null
      connected.value = false
      
      if (terminal.value) {
        if (isPersistent) {
          terminal.value.writeln('\r\n\r\nWebSocket已断开，SSH连接保持')
        } else {
          terminal.value.writeln('\r\n\r\n已断开连接')
        }
        // 断开连接后滚动到底部
        setTimeout(() => {
          if (terminal.value) {
            terminal.value.scrollToBottom()
          }
        }, 10)
      }
    }

    const cleanup = async () => {
      // 在清理前先断开连接（包括发送exit命令）
      await disconnect()
      
      // 清理定时器
      if (resizeTimer) {
        clearTimeout(resizeTimer)
        resizeTimer = null
      }
      
      // 清理 ResizeObserver
      if (resizeObserver && terminalRef.value) {
        resizeObserver.unobserve(terminalRef.value)
        resizeObserver.disconnect()
        resizeObserver = null
      }
      
      // 清理终端
      if (terminal.value) {
        try {
          terminal.value.dispose()
        } catch (error) {
          // 忽略清理错误
          console.warn('Terminal dispose warning:', error)
        }
        terminal.value = null
      }
      
      fitAddon.value = null
    }

    onMounted(() => {
      nextTick(() => {
        initTerminal()
        connect()
      })
    })

    onUnmounted(() => {
      cleanup()
    })

    return {
      terminalRef,
      disconnect,
      connected
    }
  }
}
</script>

<style scoped>
.ssh-terminal-container {
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 200px);
  height: 600px;
  background: linear-gradient(135deg, #1e1e1e 0%, #2d2d2d 100%);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.ssh-terminal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: linear-gradient(135deg, #2d2d2d 0%, #1e1e1e 100%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.ssh-status {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #8c8c8c;
  transition: all 0.3s ease;
}

.status-dot.connected {
  background: #52c41a;
  box-shadow: 0 0 8px rgba(82, 196, 26, 0.6);
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.6;
  }
}

.status-text {
  color: #fff;
  font-size: 14px;
  font-weight: 500;
}

.disconnect-btn {
  border-radius: 6px;
  font-weight: 500;
  height: 32px;
  padding: 0 16px;
  transition: all 0.3s ease;
}

.disconnect-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 8px rgba(255, 77, 79, 0.3);
}

.terminal-wrapper {
  flex: 1;
  width: 100%;
  background: #1e1e1e;
  border-radius: 0 0 12px 12px;
  overflow: hidden;
  min-height: 0;
  position: relative;
  /* 只添加左侧padding，让文字不紧贴左边界，底部保持0确保最下面一行可见 */
  padding: 0 0 0 16px;
  box-sizing: border-box;
}

.terminal-wrapper :deep(.xterm) {
  height: 100% !important;
  /* 宽度设置为100%，fitAddon会自动基于实际可用宽度计算 */
  width: 100% !important;
  position: relative;
  padding: 0;
  box-sizing: border-box;
}

.terminal-wrapper :deep(.xterm-viewport) {
  overflow-y: auto !important;
  scrollbar-width: thin;
  scrollbar-color: #4a4a4a #1a1a1a;
  border-radius: 0 0 12px 12px;
  /* 确保viewport高度正确，完全可见 */
  height: 100% !important;
  max-height: 100% !important;
}

.terminal-wrapper :deep(.xterm-screen) {
  /* 确保屏幕内容完全可见，不被底部遮挡 */
  padding-bottom: 0 !important;
  margin-bottom: 0 !important;
  height: 100% !important;
}

::v-deep .xterm {
  height: 100%;
  font-family: 'Consolas', 'Courier New', 'Monaco', monospace;
}

::v-deep .xterm-viewport {
  background-color: #1e1e1e !important;
  border-radius: 8px;
}

::v-deep .xterm-screen {
  background-color: #1e1e1e !important;
  border-radius: 8px;
  /* 确保屏幕内容完全可见 */
  height: 100% !important;
}

::v-deep .xterm-cursor-layer {
  border-radius: 2px;
}

/* 美化滚动条 */
::v-deep .xterm-viewport::-webkit-scrollbar {
  width: 8px;
}

::v-deep .xterm-viewport::-webkit-scrollbar-track {
  background: #1a1a1a;
  border-radius: 4px;
}

::v-deep .xterm-viewport::-webkit-scrollbar-thumb {
  background: #4a4a4a;
  border-radius: 4px;
}

::v-deep .xterm-viewport::-webkit-scrollbar-thumb:hover {
  background: #5a5a5a;
}
</style>
