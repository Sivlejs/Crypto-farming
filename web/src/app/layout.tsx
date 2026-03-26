import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "vGPU Platform",
  description: "Virtual GPU Management Dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-950 text-gray-100 min-h-screen">
        <nav className="border-b border-gray-800 px-6 py-3 flex items-center gap-4">
          <span className="text-lg font-bold text-indigo-400">vGPU Platform</span>
          <a href="/" className="text-sm text-gray-400 hover:text-white">
            Dashboard
          </a>
          <a href="/workers" className="text-sm text-gray-400 hover:text-white">
            Workers
          </a>
          <a href="/jobs" className="text-sm text-gray-400 hover:text-white">
            Jobs
          </a>
        </nav>
        <main className="p-6">{children}</main>
      </body>
    </html>
  );
}
