import { Component, StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";

const queryClient = new QueryClient({
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
