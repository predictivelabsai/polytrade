import {
  createContext,
  type ReactNode,
  useContext,
} from "react";

export interface Authentication {
  getToken: () => Promise<string>;
  accountControl?: ReactNode;
}

const AuthenticationContext = createContext<Authentication | null>(null);

export function AuthenticationProvider({
  children,
  value,
}: {
  children: ReactNode;
  value: Authentication;
}) {
  return <AuthenticationContext.Provider value={value}>{children}</AuthenticationContext.Provider>;
}

export function useAuthentication(): Authentication {
  const value = useContext(AuthenticationContext);
  if (!value) throw new Error("Authentication provider is missing");
  return value;
}
