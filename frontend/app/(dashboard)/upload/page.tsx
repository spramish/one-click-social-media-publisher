import type { Metadata } from "next";

import { MediaLibrary } from "@/components/media/media-library";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Video upload" };

export default function UploadPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Video upload"
        description="Bring a video into your library with metadata, previews, and validation."
      />
      <MediaLibrary />
    </div>
  );
}
