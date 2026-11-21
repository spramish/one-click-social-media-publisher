import type { Metadata } from "next";

import { AuthLayout } from "@/components/auth/auth-layout";
import { MarketingAside } from "@/components/auth/marketing-aside";
import { RegisterForm } from "@/components/auth/register-form";

export const metadata: Metadata = { title: "Create account" };

export default function RegisterPage() {
  return (
    <AuthLayout
      title="Create your account"
      subtitle="Start publishing to every platform from one dashboard."
      aside={<MarketingAside />}
    >
      <RegisterForm />
    </AuthLayout>
  );
}
