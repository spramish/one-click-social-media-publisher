import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { AuthLayout } from "@/components/auth/auth-layout";
import { LoginForm } from "@/components/auth/login-form";
import { MarketingAside } from "@/components/auth/marketing-aside";
import { LoadingState } from "@/components/ui/spinner";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <AuthLayout
      title="Sign in to your studio"
      subtitle="Welcome back. Enter your credentials to continue."
      aside={<MarketingAside />}
      footer={
        <>
          New here?{" "}
          <Link href="/register" className="font-medium text-primary underline-offset-4 hover:underline">
            Create an account
          </Link>
        </>
      }
    >
      <Suspense fallback={<LoadingState label="Loading sign-in…" />}>
        <LoginForm />
      </Suspense>
    </AuthLayout>
  );
}
