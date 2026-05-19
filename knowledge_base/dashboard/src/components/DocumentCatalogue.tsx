const DOC_TYPES: {
  index: string;
  name: string;
  description: string;
}[] = [
  {
    index: "00",
    name: "Executive Summary",
    description: "High-level overview of the integration landscape, key metrics, and strategic recommendations.",
  },
  {
    index: "01",
    name: "Integration Catalogue",
    description: "Complete inventory of all integrations, jobs, and components with classification.",
  },
  {
    index: "02",
    name: "Architecture & Data Flow",
    description: "System architecture diagrams, data flow maps, and integration topology.",
  },
  {
    index: "03",
    name: "Technical Specifications",
    description: "Detailed technical specs for each integration including schemas, transforms, and protocols.",
  },
  {
    index: "04",
    name: "Business Rules & Logic",
    description: "Extracted business rules, validation logic, and decision trees.",
  },
  {
    index: "05",
    name: "Data Dictionary & Lineage",
    description: "Field-level data dictionary with column lineage from source to target.",
  },
  {
    index: "06",
    name: "Error Handling & Recovery",
    description: "Error handling patterns, retry strategies, and recovery procedures.",
  },
  {
    index: "07",
    name: "Operations & Monitoring",
    description: "Operational procedures, scheduling, monitoring, and alerting setup.",
  },
  {
    index: "08",
    name: "Security & Compliance",
    description: "Security controls, credential management, access patterns, and compliance mapping.",
  },
  {
    index: "09",
    name: "Performance Assessment",
    description: "Performance characteristics, bottleneck analysis, and optimization opportunities.",
  },
  {
    index: "10",
    name: "Risk Assessment",
    description: "Risk inventory, severity classification, and mitigation recommendations.",
  },
  {
    index: "11",
    name: "Migration Assessment",
    description: "Migration readiness, complexity scoring, effort estimation, and recommended approach.",
  },
];

export default function DocumentCatalogue() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-mono text-slate-400 uppercase tracking-wider">
          Document Type Reference
        </h2>
        <span className="text-xs text-slate-600 font-mono">
          {DOC_TYPES.length} doc types
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {DOC_TYPES.map((doc) => (
          <div
            key={doc.index}
            className="card-dark p-4 flex flex-col gap-2 hover:border-white/[0.1] transition-colors"
          >
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-accent-primary/60 bg-accent-primary/[0.06] px-1.5 py-0.5 rounded">
                {doc.index}
              </span>
              <h3 className="text-sm font-medium text-slate-300">
                {doc.name}
              </h3>
            </div>
            <p className="text-xs text-slate-500 leading-relaxed">
              {doc.description}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
