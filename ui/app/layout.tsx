import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Synthio",
  description: "Ask questions about the Chinook music store in plain English.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
