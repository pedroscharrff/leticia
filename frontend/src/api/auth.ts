import { api } from "./client";

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const res = await api.post<TokenResponse>("/auth/login", { email, password });
  return res.data;
}

export async function portalLogin(email: string, password: string): Promise<TokenResponse> {
  const res = await api.post<TokenResponse>("/portal/auth/login", { email, password });
  return res.data;
}
