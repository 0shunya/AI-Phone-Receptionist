import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquareText, PhoneForwarded } from "lucide-react";
import { Link } from "react-router";
import { useToast } from "../components/toast";
import { Badge, Button, EmptyState, ErrorBox, PageHeader, Panel, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { localDateTime, REASON_LABEL, relative } from "../lib/format";
import type { CallRow } from "../lib/types";

export function HandoffsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const queue = useQuery({ queryKey: ["handoffs"], queryFn: api.handoffs, refetchInterval: 10_000 });
  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "claimed" | "resolved" }) => api.updateHandoff(id, status),
    onSuccess: (_d, v) => { toast.success(v.status === "claimed" ? "Claimed" : "Marked resolved"); void qc.invalidateQueries({ queryKey: ["handoffs"] }); },
    onError: (e) => toast.error(e.message),
  });
  return (
    <>
      <PageHeader title="Handoffs" description="Calls the agent transferred to a person, with the two-line summary the receptionist sees first. In a phone deployment this is where a warm transfer lands." />
      {queue.isError ? <ErrorBox error={queue.error} onRetry={() => void queue.refetch()} /> : !queue.data ? <Spinner /> : !queue.data.length ? (
        <Panel><EmptyState icon={<PhoneForwarded className="size-6" />} title="Nobody is waiting">
          Say “can I talk to a person?” on a call to see a handoff arrive here.
        </EmptyState></Panel>
      ) : (
        <ul className="flex flex-col gap-3">
          {queue.data.map((h: CallRow) => (
            <li key={h.id} className="rounded-md border border-line bg-surface p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={h.handoff_status === "waiting" ? "warn" : h.handoff_status === "claimed" ? "info" : "neutral"}>{h.handoff_status}</Badge>
                <span className="font-medium">{REASON_LABEL[h.handoff_reason ?? ""] ?? h.handoff_reason}</span>
                <span className="text-[13px] text-ink-3">{relative(h.started_at)} · {h.caller_phone ?? "withheld number"}</span>
                <div className="ml-auto flex gap-2">
                  <Link to={`/calls/${h.id}`} className="inline-flex h-7 items-center rounded-md px-2.5 text-[13px] font-medium text-ink-2 hover:bg-sunken">Transcript</Link>
                  {h.handoff_status === "waiting" && <Button size="sm" onClick={() => update.mutate({ id: h.id, status: "claimed" })}>Claim</Button>}
                  {h.handoff_status !== "resolved" && <Button size="sm" variant="primary" onClick={() => update.mutate({ id: h.id, status: "resolved" })}>Resolve</Button>}
                </div>
              </div>
              <p className="mt-2 font-mono text-[13px] whitespace-pre-line text-ink-2">{h.summary}</p>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export function TextsPage() {
  const sms = useQuery({ queryKey: ["sms"], queryFn: api.sms, refetchInterval: 15_000 });
  return (
    <>
      <PageHeader title="Texts" description="Confirmation texts for every booking, move and cancellation. No SMS provider is connected in this build, so messages are recorded here as simulated instead of being sent." />
      {sms.isError ? <ErrorBox error={sms.error} onRetry={() => void sms.refetch()} /> : !sms.data ? <Spinner /> : !sms.data.length ? (
        <Panel><EmptyState icon={<MessageSquareText className="size-6" />} title="No texts yet">Book something and its confirmation shows up here.</EmptyState></Panel>
      ) : (
        <ul className="flex flex-col gap-2">
          {sms.data.map((m) => (
            <li key={m.id} className="rounded-md border border-line bg-surface px-4 py-3">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span className="font-mono">{m.to}</span>
                <Badge tone={m.kind === "cancelled" ? "neutral" : "ok"}>{m.kind}</Badge>
                <Badge>{m.status}</Badge>
                <span className="ml-auto text-xs text-ink-3">{localDateTime(m.created_at)}</span>
              </div>
              <p className="mt-1.5">{m.body}</p>
              {m.call_id && <Link to={`/calls/${m.call_id}`} className="mt-1 inline-block text-xs text-accent hover:underline">From a phone call</Link>}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export function NotFound() {
  return (
    <Panel><EmptyState title="Page not found" action={<Link className="text-accent underline" to="/">Back to the call console</Link>}>
      That address does not exist in this app.
    </EmptyState></Panel>
  );
}
