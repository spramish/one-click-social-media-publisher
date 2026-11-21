"use client";

import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

/**
 * A date/time + IANA time-zone picker shared by the composer and the calendar.
 *
 * The value is a naive `datetime-local` string; the zone beside it is what the
 * backend reads that wall-clock time *in*. Keeping both explicit is what lets a
 * schedule mean the same instant regardless of where the browser is.
 */
export function ScheduleTimeField({
  id,
  value,
  timezone,
  onChange,
  error,
  disabled,
}: {
  id: string;
  value: string;
  timezone: string;
  onChange: (patch: { value?: string; timezone?: string }) => void;
  error?: string | null;
  disabled?: boolean;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label="Publish at" htmlFor={id} required error={error}>
        <Input
          id={id}
          type="datetime-local"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange({ value: event.target.value })}
        />
      </Field>
      <Field
        label="Time zone"
        htmlFor={`${id}-tz`}
        hint="An IANA name such as Europe/London. The time above is read in this zone."
      >
        <Input
          id={`${id}-tz`}
          value={timezone}
          disabled={disabled}
          onChange={(event) => onChange({ timezone: event.target.value })}
          placeholder="UTC"
        />
      </Field>
    </div>
  );
}

/** The viewer's own IANA zone, so a schedule defaults to the time they see. */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/**
 * Render an instant as a naive `datetime-local` string (``YYYY-MM-DDTHH:mm``) in
 * ``timeZone`` — the shape the input needs, and the time the user originally saw.
 */
export function instantToLocalInput(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date);
    const get = (type: Intl.DateTimeFormatPartTypes) =>
      parts.find((part) => part.type === type)?.value ?? "";
    return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
  } catch {
    return "";
  }
}
