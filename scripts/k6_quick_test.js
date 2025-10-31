import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  thresholds: {
    http_req_failed: ['rate<0.01'], // <1% errors
    http_req_duration: ['p(95)<1800'], // P95 < 1.8s
    checks: ['rate>0.99'],
  },
  scenarios: {
    spike_2x: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '10s', target: 20 },
        { duration: '10s', target: 40 }, // ~2x spike
        { duration: '20s', target: 40 },
        { duration: '10s', target: 0 },
      ],
      gracefulRampDown: '5s',
    },
  },
};

const BASE = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  const endpoints = [
    `${BASE}/api/v1/health`,
    `${BASE}/api/v1/ai/stats`,
    `${BASE}/api/v1/metrics/history?limit=30`,
    `${BASE}/api/v1/dashboard/insight`,
  ];
  // random pick to mix
  const url = endpoints[Math.floor(Math.random() * endpoints.length)];
  const res = http.get(url, { tags: { endpoint: url } });
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
  sleep(Math.random() * 0.2);
}

