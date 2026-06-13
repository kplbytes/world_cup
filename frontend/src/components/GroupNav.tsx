export default function GroupNav({ selected, onSelect }: { selected: string; onSelect: (group: string) => void }) {
  return <nav className="group-nav" aria-label="小组选择">
    {[..."ABCDEFGHIJKL"].map((group) => <button key={group} className={selected === group ? "active" : ""} onClick={() => onSelect(group)} aria-pressed={selected === group}>{group}</button>)}
  </nav>;
}

