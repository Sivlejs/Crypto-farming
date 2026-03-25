import StatsCard from "@/components/StatsCard";
import WorkersTable from "@/components/WorkersTable";

export const revalidate = 10;

async function getData() {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  try {
    const [workersRes, jobsRes, gpusRes] = await Promise.all([
      fetch(`${apiUrl}/workers`, { next: { revalidate: 10 } }),
      fetch(`${apiUrl}/jobs`, { next: { revalidate: 10 } }),
      fetch(`${apiUrl}/inventory/gpus`, { next: { revalidate: 10 } }),
    ]);
    const workers = workersRes.ok ? await workersRes.json() : [];
    const jobs = jobsRes.ok ? await jobsRes.json() : [];
    const gpus = gpusRes.ok ? await gpusRes.json() : [];
    return { workers, jobs, gpus };
  } catch {
    return { workers: [], jobs: [], gpus: [] };
  }
}

export default async function HomePage() {
  const { workers, jobs, gpus } = await getData();
  const online = workers.filter((w: { status: string }) => w.status === "online").length;
  const pending = jobs.filter((j: { status: string }) => j.status === "pending").length;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Cluster Overview</h1>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <StatsCard label="Total Workers" value={workers.length} />
        <StatsCard label="Online Workers" value={online} highlight />
        <StatsCard label="Total GPUs" value={gpus.length} />
        <StatsCard label="Pending Jobs" value={pending} />
      </div>
      <h2 className="text-xl font-semibold mb-4">Workers</h2>
      <WorkersTable workers={workers} />
    </div>
  );
}
