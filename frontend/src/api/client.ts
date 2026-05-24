import axios, { AxiosError } from "axios";

const BASE_URL = import.meta.env.VITE_API_URL ?? "";

export const api = axios.create({
  baseURL: BASE_URL,
  headers: { "Content-Type": "application/json" },
  timeout: 15_000,
});

// Attach JWT on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Redirect to login on 401
api.interceptors.response.use(
  (res) => res,
  (err: AxiosError) => {
    if (err.response?.status === 401) {
      localStorage.removeItem("access_token");
      const path = window.location.pathname;
      const isAdminArea =
        path.startsWith("/login") ||
        path.startsWith("/dashboard") ||
        path.startsWith("/tenants") ||
        path.startsWith("/settings") ||
        path.startsWith("/chat-test");
      window.location.href = isAdminArea ? "/login" : "/portal/login";
    }
    return Promise.reject(err);
  }
);
