import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({ baseURL: '/api', timeout: 120000 })

http.interceptors.response.use(
  (resp) => resp.data,
  (error) => {
    const data = error.response?.data
    const detail = typeof data?.detail === 'string' ? data.detail : data?.detail ? JSON.stringify(data.detail) : error.message
    if (!error.config?.silent) {
      if (data?.code === 'mcp_auth') {
        ElMessage({ type: 'warning', message: `${detail}（请到设置页授权腾讯自选股）`, duration: 5000 })
      } else {
        ElMessage.error(detail || '请求失败')
      }
    }
    return Promise.reject(Object.assign(error, { detail }))
  },
)

declare module 'axios' {
  interface AxiosRequestConfig {
    silent?: boolean
  }
}

export default http
