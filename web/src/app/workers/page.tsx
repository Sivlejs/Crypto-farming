export const revalidate = 10;

async function getWorkers() {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  try {
    const res = await fetch(`${apiUrl}/workers`, { next: { revalidate: 10 } });
    return res.ok ? res.json() : [];
  } catch {
    return [];
  }
}

export default async function WorkersPage() {
  const workers = await getWorkers();
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Workers</h1>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-gray-700 text-gray-400">
              <th className="text-left py-2 pr-4">Name</th>
              <th className="text-left py-2 pr-4">Status</th>
              <th className="text-left py-2 pr-4">Hostname</th>
              <th className="text-left py-2 pr-4">Last Heartbeat</th>
            </tr>
          </thead>
          <tbody>
            {workers.map(
              (w: {
                id: string;
                name: string;
                status: string;
                hostname?: string;
                last_heartbeat?: string;
              }) => (
                <tr key={w.id} className="border-b border-gray-800 hover:bg-gray-900">
                  <td className="py-2 pr-4 font-mono">{w.name}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={`px-2 py-0.5 rounded text-xs ${
                        w.status === "online"
                          ? "bg-green-900 text-green-300"
                          : "bg-gray-800 text-gray-400"
                      }`}
                    >
                      {w.status}
                    </span>
                  </td>
                  <td className="py-2 pr-4 text-gray-400">{w.hostname ?? "—"}</td>
                  <td className="py-2 pr-4 text-gray-400 text-xs">{w.last_heartbeat ?? "—"}</td>
                </tr>
              ),
            )}
            {workers.length === 0 && (
              <tr>
                <td colSpan={4} className="py-4 text-gray-500 text-center">
                  No workers registered
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
