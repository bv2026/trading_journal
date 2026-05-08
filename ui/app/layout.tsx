import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Trading Journal",
  description: "Trading Journal dashboard"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
