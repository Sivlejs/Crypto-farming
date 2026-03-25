export const revalidate = 10;

async function getJobs() {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  try {
    const res = await fetch(`${apiUrl}/jobs`, { next: { revalidate: 10 } });
    return res.ok ? res.json() : [];
  } catch {
    return [];
  }
}

const statusColor: Record<string, string> = {
  pending: "bg-yellow-900 text-yellow-300",
  running: "bg-blue-900 text-blue-300",
  succeeded: "bg-green-900 text-green-300",
  failed: "bg-red-900 text-red-300",
  cancelled: "bg-gray-800 text-gray-400",
  assigned: "bg-indigo-900 text-indigo-300",
};

export default async function JobsPage() {
  const jobs = await getJobs();
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Jobs</h1>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-gray-700 text-gray-400">
              <th className="text-left py-2 pr-4">Name</th>
              <th className="text-left py-2 pr-4">Image</th>
              <th className="text-left py-2 pr-4">Status</th>
              <th className="text-left py-2 pr-4">GPUs</th>
              <th className="text-left py-2 pr-4">Created</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map(
              (j: {
                id: string;
                name: string;
                image: string;
                status: string;
                gpu_count: number;
                created_at: string;
              }) => (
                <tr key={j.id} className="border-b border-gray-800 hover:bg-gray-900">
                  <td className="py-2 pr-4 font-mono text-sm">{j.name}</td>
                  <td className="py-2 pr-4 text-gray-400 text-xs font-mono">{j.image}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={`px-2 py-0.5 rounded text-xs ${
                        statusColor[j.status] ?? "bg-gray-800 text-gray-400"
                      }`}
                    >
                      {j.status}
                    </span>
                  </td>
                  <td className="py-2 pr-4">{j.gpu_count}</td>
                  <td className="py-2 pr-4 text-gray-400 text-xs">
                    {new Date(j.created_at).toLocaleString()}
                  </td>
                </tr>
              ),
            )}
            {jobs.length === 0 && (
              <tr>
                <td colSpan={5} className="py-4 text-gray-500 text-center">
                  No jobs submitted
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
