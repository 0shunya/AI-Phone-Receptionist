import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarPlus, ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useToast } from "../components/toast";
import { Badge, Button, cx, Dialog, ErrorBox, Label, PageHeader, Panel, Select, Spinner, TextInput } from "../components/ui";
import { api } from "../lib/api";
import { addDays, dayLabel, isoDay, localDateTime, localTime, minutesOfDay } from "../lib/format";
import type { Booking } from "../lib/types";

const START_MIN = 9 * 60;
const END_MIN = 20 * 60;
const PX_PER_MIN = 1.1;

export default function CalendarPage() {
  const [day, setDay] = useState(() => isoDay(new Date()));
  const [showCancelled, setShowCancelled] = useState(false);
  const [selected, setSelected] = useState<Booking | null>(null);
  const [creating, setCreating] = useState(false);
  const staff = useQuery({ queryKey: ["staff"], queryFn: api.staff });
  const bookings = useQuery({ queryKey: ["bookings", day, showCancelled], queryFn: () => api.bookings(day, day, showCancelled),
    refetchInterval: 15_000 });
  const weekday = (new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7;   // 0 = Monday, like the API

  const hours = useMemo(() => Array.from({ length: (END_MIN - START_MIN) / 60 + 1 }, (_, i) => START_MIN / 60 + i), []);

  return (
    <>
      <PageHeader title="Calendar" description="The bookings table is the calendar. Postgres refuses any two live bookings that overlap for one staff member."
        actions={<Button variant="primary" icon={<CalendarPlus className="size-4" />} onClick={() => setCreating(true)}>New booking</Button>} />
      <Panel
        title={<span className="flex items-center gap-2">
          <Button size="sm" variant="ghost" aria-label="Previous day" onClick={() => setDay(addDays(day, -1))} icon={<ChevronLeft className="size-4" />} />
          <span className="min-w-52 text-center">{dayLabel(day)}</span>
          <Button size="sm" variant="ghost" aria-label="Next day" onClick={() => setDay(addDays(day, 1))} icon={<ChevronRight className="size-4" />} />
        </span>}
        actions={<>
          <label className="flex items-center gap-1.5 text-[13px] text-ink-2">
            <input type="checkbox" checked={showCancelled} onChange={(e) => setShowCancelled(e.target.checked)} /> Show cancelled
          </label>
          <TextInput type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} className="h-7 w-36 text-[13px]" aria-label="Pick a day" />
          <Button size="sm" onClick={() => setDay(isoDay(new Date()))}>Today</Button>
        </>}
        bodyClassName="overflow-x-auto"
      >
        {staff.isError || bookings.isError ? <div className="p-4"><ErrorBox error={staff.error ?? bookings.error} onRetry={() => void bookings.refetch()} /></div>
          : !staff.data || !bookings.data ? <Spinner label="Loading calendar" /> : (
            <div className="grid min-w-[640px]" style={{ gridTemplateColumns: `56px repeat(${staff.data.length}, minmax(0, 1fr))` }}>
              <div className="border-b border-line" />
              {staff.data.map((s) => {
                const shift = s.shifts.find((sh) => sh.weekday === weekday);
                return (
                  <div key={s.id} className="border-b border-l border-line px-3 py-2">
                    <p className="font-medium">{s.name}</p>
                    <p className="text-xs text-ink-3">{shift ? `${shift.start}–${shift.end}` : "Not working"}</p>
                  </div>
                );
              })}
              <div className="relative" style={{ height: (END_MIN - START_MIN) * PX_PER_MIN }}>
                {hours.map((h) => (
                  <span key={h} className="num absolute right-2 -translate-y-1/2 text-[11px] text-ink-3" style={{ top: (h * 60 - START_MIN) * PX_PER_MIN }}>
                    {h}:00
                  </span>
                ))}
              </div>
              {staff.data.map((s) => {
                const shift = s.shifts.find((sh) => sh.weekday === weekday);
                const toMin = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
                return (
                  <div key={s.id} className="relative border-l border-line" style={{ height: (END_MIN - START_MIN) * PX_PER_MIN }}>
                    {hours.map((h) => <div key={h} className="absolute inset-x-0 border-t border-line/70" style={{ top: (h * 60 - START_MIN) * PX_PER_MIN }} />)}
                    {!shift && <div className="absolute inset-0 bg-sunken/70" />}
                    {shift && <>
                      <div className="absolute inset-x-0 top-0 bg-sunken/70" style={{ height: Math.max(0, toMin(shift.start) - START_MIN) * PX_PER_MIN }} />
                      <div className="absolute inset-x-0 bottom-0 bg-sunken/70" style={{ height: Math.max(0, END_MIN - toMin(shift.end)) * PX_PER_MIN }} />
                    </>}
                    {bookings.data.filter((b) => b.staff_id === s.id).map((b) => {
                      const top = (minutesOfDay(b.starts_at) - START_MIN) * PX_PER_MIN;
                      const height = Math.max(18, (new Date(b.ends_at).getTime() - new Date(b.starts_at).getTime()) / 60000 * PX_PER_MIN - 2);
                      return (
                        <button key={b.id} onClick={() => setSelected(b)} style={{ top, height }}
                          className={cx("absolute inset-x-1 overflow-hidden rounded border px-1.5 py-0.5 text-left text-[11px] leading-tight",
                            b.status === "cancelled" ? "border-line bg-surface text-ink-3 line-through"
                              : b.call_id ? "border-accent/30 bg-accent-soft text-accent hover:border-accent" : "border-line-strong bg-surface hover:border-accent")}>
                          <span className="font-semibold">{localTime(b.starts_at)}</span> {b.service_name} · {b.customer}
                        </button>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          )}
      </Panel>
      <p className="mt-2 text-xs text-ink-3">Tinted blocks were booked by the phone agent; white ones were made from this console. Times are salon local time (Asia/Kolkata).</p>
      {selected && <BookingDialog booking={selected} onClose={() => setSelected(null)} />}
      {creating && <NewBookingDialog day={day} onClose={() => setCreating(false)} />}
    </>
  );
}

function BookingDialog({ booking, onClose }: { booking: Booking; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [moveDay, setMoveDay] = useState(() => isoDay(new Date(booking.starts_at)));
  const [newSlot, setNewSlot] = useState("");
  const slots = useQuery({ queryKey: ["availability", booking.service, moveDay], queryFn: () => api.availability(booking.service, moveDay, moveDay),
    enabled: booking.status === "confirmed" });
  const done = (message: string) => { toast.success(message); void qc.invalidateQueries({ queryKey: ["bookings"] }); void qc.invalidateQueries({ queryKey: ["sms"] }); onClose(); };
  const cancel = useMutation({ mutationFn: () => api.cancelBooking(booking.id, "cancelled from console"), onSuccess: () => done("Booking cancelled"),
    onError: (e) => toast.error(e.message) });
  const move = useMutation({ mutationFn: () => api.rescheduleBooking(booking.id, newSlot), onSuccess: () => done("Booking moved"),
    onError: (e) => toast.error(e.message) });
  return (
    <Dialog open title={`${booking.service_name} for ${booking.customer}`} onClose={onClose} footer={<>
      <Button onClick={onClose}>Close</Button>
      {booking.status === "confirmed" && <Button variant="danger" loading={cancel.isPending} onClick={() => cancel.mutate()}>Cancel booking</Button>}
    </>}>
      <dl className="grid grid-cols-[110px_1fr] gap-y-1.5 text-[13px]">
        <dt className="text-ink-3">When</dt><dd>{localDateTime(booking.starts_at)} – {localTime(booking.ends_at)}</dd>
        <dt className="text-ink-3">Staff</dt><dd>{booking.staff}</dd>
        <dt className="text-ink-3">Phone</dt><dd className="font-mono">{booking.phone_e164}</dd>
        <dt className="text-ink-3">Status</dt><dd><Badge tone={booking.status === "confirmed" ? "ok" : "neutral"}>{booking.status}</Badge></dd>
        <dt className="text-ink-3">Source</dt><dd>{booking.call_id ? <Link className="text-accent underline" to={`/calls/${booking.call_id}`}>Phone call</Link> : "Console"}</dd>
        <dt className="text-ink-3">Event id</dt><dd className="truncate font-mono text-[11px]">{booking.event_id}</dd>
      </dl>
      {booking.status === "confirmed" && (
        <div className="mt-4 border-t border-line pt-4">
          <p className="mb-2 text-xs font-medium text-ink-2">Move to another time</p>
          <div className="grid grid-cols-2 gap-2">
            <TextInput type="date" value={moveDay} onChange={(e) => { setMoveDay(e.target.value); setNewSlot(""); }} aria-label="New day" />
            <Select value={newSlot} onChange={(e) => setNewSlot(e.target.value)} aria-label="New time">
              <option value="">{slots.isLoading ? "Loading…" : slots.data?.length ? "Pick a time" : "No free times"}</option>
              {slots.data?.map((s) => <option key={s.slot_id} value={s.slot_id}>{localTime(s.starts_at)} · {s.staff}</option>)}
            </Select>
          </div>
          <Button className="mt-2" size="sm" disabled={!newSlot} loading={move.isPending} onClick={() => move.mutate()}>Move booking</Button>
        </div>
      )}
    </Dialog>
  );
}

function NewBookingDialog({ day, onClose }: { day: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const services = useQuery({ queryKey: ["services"], queryFn: api.services });
  const [service, setService] = useState("haircut");
  const [onDay, setOnDay] = useState(day);
  const [slotId, setSlotId] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [key] = useState(() => crypto.randomUUID());
  const slots = useQuery({ queryKey: ["availability", service, onDay], queryFn: () => api.availability(service, onDay, onDay) });
  const create = useMutation({
    mutationFn: () => api.createBooking({ slot_id: slotId, name: name.trim(), phone_e164: phone.trim() }, key),
    onSuccess: () => { toast.success("Booked, confirmation text queued"); void qc.invalidateQueries({ queryKey: ["bookings"] }); void qc.invalidateQueries({ queryKey: ["sms"] }); onClose(); },
    onError: (e) => { toast.error(e.message); void slots.refetch(); },
  });
  return (
    <Dialog open title="New booking" onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="primary" loading={create.isPending} disabled={!slotId || !name.trim() || !phone.trim()} onClick={() => create.mutate()}>Book</Button>
    </>}>
      <div className="grid grid-cols-2 gap-3">
        <div><Label htmlFor="nb-service">Service</Label>
          <Select id="nb-service" value={service} onChange={(e) => { setService(e.target.value); setSlotId(""); }}>
            {services.data?.map((s) => <option key={s.code} value={s.code}>{s.name} ({s.duration_min} min)</option>)}
          </Select></div>
        <div><Label htmlFor="nb-day">Day</Label>
          <TextInput id="nb-day" type="date" value={onDay} onChange={(e) => { setOnDay(e.target.value); setSlotId(""); }} /></div>
        <div className="col-span-2"><Label htmlFor="nb-slot">Free time</Label>
          <Select id="nb-slot" value={slotId} onChange={(e) => setSlotId(e.target.value)}>
            <option value="">{slots.isLoading ? "Loading…" : slots.data?.length ? "Pick a time" : "No free times that day"}</option>
            {slots.data?.map((s) => <option key={s.slot_id} value={s.slot_id}>{localTime(s.starts_at)} · {s.staff}</option>)}
          </Select></div>
        <div><Label htmlFor="nb-name">Customer name</Label><TextInput id="nb-name" value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div><Label htmlFor="nb-phone">Phone</Label><TextInput id="nb-phone" inputMode="tel" placeholder="+91 98765 43210" value={phone} onChange={(e) => setPhone(e.target.value)} /></div>
      </div>
    </Dialog>
  );
}
