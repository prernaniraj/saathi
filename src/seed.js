const all = [1, 1, 1, 1, 1];
export const seed = () => ({
  seq: 100,
  caregivers: [
    { id: 'c1', name: 'Anita', city: 'Hyderabad', stage: 'Applied', modules: [0, 0, 0, 0, 0] },
    { id: 'c2', name: 'Ravi', city: 'Hyderabad', stage: 'Training', modules: [1, 1, 0, 0, 0] },
    { id: 'c3', name: 'Meena', city: 'Bengaluru', stage: 'Training', modules: [1, 1, 1, 1, 0] },
    { id: 'c4', name: 'Suresh', city: 'Chennai', stage: 'Certified', modules: [...all] },
    { id: 'c5', name: 'Lakshmi', city: 'Hyderabad', stage: 'Certified', modules: [...all] },
    { id: 'c6', name: 'Imran', city: 'Pune', stage: 'Matched', modules: [...all] }
  ],
  requests: [
    { id: 'r1', family: 'Menon family, Dallas', parent: 'Mr. Raghavan', city: 'Pune', visitsPerWeek: 2, need: 'Companionship', status: 'Matched', caregiverId: 'c6' },
    { id: 'r2', family: 'Rao family, Austin', parent: 'Mrs. Rao', city: 'Hyderabad', visitsPerWeek: 3, need: 'Medical visit escort', status: 'Open', caregiverId: null },
    { id: 'r3', family: 'Iyer family, Edison NJ', parent: 'Mr. Iyer', city: 'Chennai', visitsPerWeek: 2, need: 'Daily check-in', status: 'Open', caregiverId: null }
  ],
  visits: [
    { id: 'v1', requestId: 'r1', caregiverId: 'c6', date: '2026-11-12', mood: 'Good', note: 'Walked in the garden and read the newspaper together. Ate lunch well.' }
  ]
});
