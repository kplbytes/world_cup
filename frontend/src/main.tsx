import { Component, StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error, query) => {
      // P3-D: centralized query error logging. Non-4xx errors are more
      // actionable (server/network issues) so we log them at warn level.
      // 4xx errors (esp. 404) are expected for some flows and are logged
      // at debug level to avoid console noise.
      const status = (error as { status?: number }).status ?? 0;
      if (status >= 400 && status < 500) {
        console.debug("[query] client error", status, query.queryKey, error.message);
      } else {
        console.warn("[query] error", query.queryKey, error.message);
      }
    },
  }),
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      // P3-D: mutations carry user intent (refresh, trigger workflow) —
      // log at warn so failures surface in dev consoles / log shippers.
      const key = mutation.options.mutationKey;
      console.warn("[mutation] error", key, error.message);
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Don't retry on client errors (4xx) that won't change with a retry
        const status = (error as { status?: number }).status;
        if (typeof status === "number" && status >= 400 && status < 500) return false;
        return failureCount < 2;
      },
    },
    mutations: {
      retry: false,
    },
  },
});

interface ErrorBoundaryState {
  hasError: boolean;
}

/**
 * Global error boundary — prevents a single component crash (e.g. unexpected
 * null field from the API) from blanking the entire app. Shows a recovery
 * screen with a reload button instead of a white screen.
 */
class GlobalErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: { componentStack?: string }) {
    console.error("Uncaught error in React tree:", error, info?.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="state-screen error">
          <span>应用遇到意外错误</span>
          <button onClick={() => window.location.reload()}>刷新页面</button>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <GlobalErrorBoundary>
        <App />
      </GlobalErrorBoundary>
    </QueryClientProvider>
  </StrictMode>
);
