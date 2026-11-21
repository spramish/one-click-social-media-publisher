import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { AuthCard } from "@/components/auth/auth-layout";
import { ResetPasswordForm } from "@/components/auth/reset-password-form";
import { LoadingState } from "@/components/ui/spinner";

export const metadata: Metadata = { title: "Set a new password" };

export default function ResetPasswordPage() {
  return (
    <AuthCard
      title="Choose a new password"
      subtitle="Pick something you have not used before."
      footer={
        <>
          Need a new link?{" "}
          <Link
            href="/forgot-password"
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            Request again
          </Link>
        </>
      }
    >
      <Suspense fallback={<LoadingState label="Loading…" />}>
        <ResetPasswordForm />
      </Suspense>
    </AuthCard>
  );
}
