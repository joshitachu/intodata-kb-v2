import { useTemplates } from "../../hooks/useTemplates";

/**
 * Auto-generated reference of the 12 document types produced by the pipeline.
 * Links each doc type to its output template for quick navigation.
 */

const DOC_TYPES: {
  index: string;
  name: string;
  description: string;
  templateSlug: string;
}[] = [
  {
    index: "00",
    name: "Executive Summary",
    description: "High-level overview of the integration landscape, key metrics, and strategic recommendations.",
    templateSlug: "output/00_executive_summary",
  },
  {
    index: "01",
    name: "Integration Catalogue",
    description: "Complete inventory of all integrations, jobs, and components with classification.",
    templateSlug: "output/01_integration_catalogue",
  },
  {
    index: "02",
    name: "Architecture & Data Flow",
    description: "System architecture diagrams, data flow maps, and integration topology.",
    templateSlug: "output/02_architecture_data_flow",
  },
  {
    index: "03",
    name: "Technical Specifications",
    description: "Detailed technical specs for each integration including schemas, transforms, and protocols.",
    templateSlug: "output/03_technical_specifications",
  },
  {
    index: "04",
    name: "Business Rules & Logic",
    description: "Extracted business rules, validation logic, and decision trees.",
    templateSlug: "output/04_business_rules_logic",
  },
  {
    index: "05",
    name: "Data Dictionary & Lineage",
    description: "Field-level data dictionary with column lineage from source to target.",
    templateSlug: "output/05_data_dictionary_lineage",
  },
  {
    index: "06",
    name: "Error Handling & Recovery",
    description: "Error handling patterns, retry strategies, and recovery procedures.",
    templateSlug: "output/06_error_handling_recovery",
  },
  {
    index: "07",
    name: "Operations & Monitoring",
    description: "Operational procedures, scheduling, monitoring, and alerting setup.",
    templateSlug: "output/07_operations_monitoring",
  },
  {
    index: "08",
    name: "Security & Compliance",
    description: "Security controls, credential management, access patterns, and compliance mapping.",
    templateSlug: "output/08_security_compliance",
  },
  {
    index: "09",
    name: "Performance Assessment",
    description: "Performance characteristics, bottleneck analysis, and optimization opportunities.",
    templateSlug: "output/09_performance_assessment",
  },
  {
    index: "10",
    name: "Risk Assessment",
    description: "Risk inventory, severity classification, and mitigation recommendations.",
    templateSlug: "output/10_risk_assessment",
  },
  {
    index: "11",
    name: "Migration Assessment",
    description: "Migration readiness, complexity scoring, effort estimation, and recommended approach.",
    templateSlug: "output/11_migration_assessment",
  },
];

interface Props {
  onNavigateToTemplate?: (slug: string) => void;
}

export default function DocumentCatalogue({ onNavigateToTemplate }: Props) {
  const { data: templatesData } = useTemplates({ category: "output" });
  const templates = templatesData?.templates ?? [];

  const templateMap = new Map(templates.map((t) => [t.slug, t]));

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
        {DOC_TYPES.map((doc) => {
          const tpl = templateMap.get(doc.templateSlug);
          return (
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
              <div className="flex items-center justify-between mt-auto pt-2 border-t border-white/[0.04]">
                <span className="text-xs font-mono text-slate-600 truncate" title={doc.templateSlug}>
                  {doc.templateSlug}
                </span>
                <div className="flex items-center gap-2 shrink-0">
                  {tpl ? (
                    <>
                      <span className="text-xs text-slate-600 font-mono">
                        v{tpl.latest_version ?? 1}
                      </span>
                      {onNavigateToTemplate && (
                        <button
                          className="text-xs text-accent-primary/70 hover:text-accent-primary transition-colors"
                          onClick={() => onNavigateToTemplate(doc.templateSlug)}
                        >
                          Edit Template
                        </button>
                      )}
                    </>
                  ) : (
                    <span className="text-xs text-slate-600 italic">
                      filesystem
                    </span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
