import "./SubNav.css";

interface SubNavProps {
  title: string;
  action?: { label: string; onClick: () => void };
}

export function SubNav({ title, action }: SubNavProps) {
  return (
    <div className="sub-nav" role="navigation" aria-label="Sub-navigation">
      <div className="sub-nav__inner">
        <span className="sub-nav__title">{title}</span>
        {action && (
          <button className="btn-primary sub-nav__cta" onClick={action.onClick}>
            {action.label}
          </button>
        )}
      </div>
    </div>
  );
}
