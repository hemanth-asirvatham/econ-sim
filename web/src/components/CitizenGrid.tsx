import type { CitizenSnapshot } from "../types";

interface CitizenGridProps {
  citizens: CitizenSnapshot[];
  activeCitizenId?: string;
  onSelect: (citizenId: string) => void;
}

export function CitizenGrid({ citizens, activeCitizenId, onSelect }: CitizenGridProps) {
  return (
    <section className="citizen-grid">
      {citizens.map((citizen) => (
        <button
          key={citizen.citizen_id}
          className={`citizen-card ${citizen.citizen_id === activeCitizenId ? "citizen-card--active" : ""}`}
          onClick={() => onSelect(citizen.citizen_id)}
          aria-pressed={citizen.citizen_id === activeCitizenId}
        >
          <div className={`citizen-card__portrait citizen-card__portrait--${citizen.approval_band}`}>
            <div className="citizen-card__portrait-figure" />
          </div>
          <div className="citizen-card__meta">
            <div className="citizen-card__identity">
              <strong>{citizen.display_name}</strong>
              <span>{citizen.role}</span>
            </div>
            <span className="citizen-card__region">{citizen.region}</span>
          </div>
          <div className="citizen-card__signal">
            <span className={`citizen-card__tag citizen-card__tag--${citizen.approval_band}`}>{citizen.support_label}</span>
            <small>{citizen.voice}</small>
          </div>
          <div className="citizen-card__details">
            <span>{citizen.mood}</span>
            <span>{citizen.ai_exposure}</span>
          </div>
          <p className="citizen-card__update">{citizen.current_update}</p>
          <div className="citizen-card__footer">
            <span>{citizen.citizen_id === activeCitizenId ? "Interview open" : "Open interview"}</span>
          </div>
        </button>
      ))}
    </section>
  );
}
