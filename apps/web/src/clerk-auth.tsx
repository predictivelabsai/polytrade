import {
  ClerkProvider,
  Show,
  SignInButton,
  UserButton,
  useAuth,
} from "@clerk/react";
import {
  type ReactNode,
  useCallback,
  useMemo,
} from "react";

import {
  AuthenticationProvider,
  type Authentication,
} from "./auth";
import { env } from "./env";

// Local/E2E-only seam: VITE_E2E_AUTH_BYPASS=1 (never set in deployed builds)
// renders the app without Clerk sign-in so tools like Playwright can
// screenshot authenticated pages. getToken returns a meaningless token —
// HTTP clients still issue real requests, which test tooling intercepts.
const e2eAuthBypass = import.meta.env.VITE_E2E_AUTH_BYPASS === "1";
const e2eAuthentication: Authentication = {
  getToken: async () => "e2e-auth-bypass",
};

function ClerkAuthentication({ children }: { children: ReactNode }) {
  const { getToken, isLoaded } = useAuth();
  const tokenProvider = useCallback(async () => {
    if (!isLoaded) throw new Error("Standalone authentication is still loading");
    const token = await getToken({ template: env.VITE_CLERK_JWT_TEMPLATE });
    if (!token) throw new Error("Standalone session is unavailable");
    return token;
  }, [getToken, isLoaded]);
  const value = useMemo<Authentication>(
    () => ({ getToken: tokenProvider, accountControl: <UserButton /> }),
    [tokenProvider],
  );
  return <AuthenticationProvider value={value}>{children}</AuthenticationProvider>;
}

export default function StandaloneAuthentication({ children }: { children: ReactNode }) {
  const signIn = (
    <main className="sign-in-shell">
      <section className="sign-in-card" aria-labelledby="sign-in-heading">
        <span className="eyebrow">PolyTrade · standalone</span>
        <h1 id="sign-in-heading">Enter the decision desk.</h1>
        <p>Sign in to research Polymarket. Connecting a wallet remains a separate step.</p>
        <SignInButton mode="modal">
          <button className="button button-primary" type="button">Sign in</button>
        </SignInButton>
      </section>
    </main>
  );

  if (e2eAuthBypass) {
    return <AuthenticationProvider value={e2eAuthentication}>{children}</AuthenticationProvider>;
  }

  return (
    <ClerkProvider
      publishableKey={env.VITE_CLERK_PUBLISHABLE_KEY}
      appearance={{
        variables: {
          colorBackground: "#0B1B14",
          colorNeutral: "#E7F5EC",
          colorPrimary: "#34D399",
          borderRadius: "8px",
          fontFamily: "Manrope Variable, sans-serif",
        },
        elements: {
          cardBox: { boxShadow: "0 18px 60px rgba(0, 0, 0, 0.42)" },
          card: { border: "1px solid #2B4B3B" },
          formFieldInput: { backgroundColor: "#06110D", borderColor: "#2B4B3B" },
          footerActionLink: { color: "#34D399" },
        },
      }}
    >
      <Show when="signed-in" fallback={signIn} treatPendingAsSignedOut>
        <ClerkAuthentication>{children}</ClerkAuthentication>
      </Show>
    </ClerkProvider>
  );
}
