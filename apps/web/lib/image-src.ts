import type { ContentBlock } from "./types";

/**
 * Return the public image URL only when the media has been materialized
 * locally. Deliberately **never falls back to the source (platform) URL**:
 * a missing local file means ingestion did not successfully collect the
 * image and we must not mask that failure by attempting a remote load
 * (which typically fails anyway due to hotlink protection and network
 * restrictions, and hides data-quality issues).
 */
export function resolveImageSrc(
  storagePath: string | null | undefined,
  _sourceUrl?: string | null | undefined,
): string {
  return storagePath ?? "";
}

/** Admin/OCR only variant that falls back to source_url for tooling views. */
export function resolveImageSrcWithRemoteFallback(
  storagePath: string | null | undefined,
  sourceUrl: string | null | undefined,
): string {
  if (storagePath) return storagePath;
  return sourceUrl ?? "";
}

export function imageBlockSrc(block: Pick<ContentBlock, "storage_path" | "source_url">): string {
  return resolveImageSrc(block.storage_path);
}

/** Admin/OCR only variant for ``ContentBlock`` inputs. */
export function imageBlockSrcWithRemoteFallback(
  block: Pick<ContentBlock, "storage_path" | "source_url">,
): string {
  return resolveImageSrcWithRemoteFallback(block.storage_path, block.source_url);
}
