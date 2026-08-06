/**
 * Top navigation bar: view switching, data-source selection and favourites entry.
 */
import { Database, Menu, RotateCcw, SlidersHorizontal } from "lucide-react";

interface Props {
  filterCount: number;
  filtersExpanded?: boolean;
  filtersDisabled?: boolean;
  resetDisabled: boolean;
  dataSources: string[];
  selectedDataSources: string[];
  online: boolean;
  onOpenNavigation: () => void;
  onOpenFilters: () => void;
  onReset: () => void;
  onToggleDataSource: (source: string) => void;
}

export default function AppHeader({
  filterCount,
  filtersExpanded = false,
  filtersDisabled = false,
  resetDisabled,
  dataSources,
  selectedDataSources,
  online,
  onOpenNavigation,
  onOpenFilters,
  onReset,
  onToggleDataSource,
}: Props) {
  const selected = new Set(selectedDataSources);
  return (
    <header className="header-bar app-header">
      <button
        type="button"
        className="mobile-menu-trigger"
        onClick={onOpenNavigation}
        aria-label="Open navigation"
      >
        <Menu size={20} />
      </button>
      <h1 className="header-title">Foundation Intelligence Platform</h1>
      <div className="header-actions" aria-label="Page controls">
        <button
          type="button"
          className="app-header-action app-header-filter"
          onClick={onOpenFilters}
          aria-expanded={filtersExpanded}
          disabled={filtersDisabled}
        >
          <SlidersHorizontal size={16} aria-hidden="true" />
          <span>Filters</span>
          {filterCount > 0 && <b aria-label={`${filterCount} active filters`}>{filterCount}</b>}
        </button>
        <button
          type="button"
          className="app-header-action app-header-reset"
          onClick={onReset}
          disabled={resetDisabled}
        >
          <RotateCcw size={15} aria-hidden="true" />
          <span>Reset</span>
        </button>
        <details className="data-sources-disclosure">
          <summary className="app-header-action" aria-label="Data sources">
            <Database size={15} aria-hidden="true" />
            <span>Data sources</span>
          </summary>
          <div className="data-sources-panel">
            <strong>Data sources</strong>
            <span>
              This scope persists across navigation and refresh. Deselecting every source intentionally returns no source-backed results.
            </span>
            {!online && <small>Selections are retained while the backend is offline.</small>}
            <div className="data-source-options" aria-label="Data source selection">
              {dataSources.map(source => (
                <button
                  type="button"
                  key={source}
                  className={`data-source-option${selected.has(source) ? " selected" : ""}`}
                  aria-pressed={selected.has(source)}
                  onClick={() => onToggleDataSource(source)}
                >
                  {source}
                </button>
              ))}
            </div>
          </div>
        </details>
      </div>
    </header>
  );
}
