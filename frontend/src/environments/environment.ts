// Ambiente de desenvolvimento (`ng serve`, porta 4200) — o backend FastAPI roda separado
// (porta 5050, CORS liberado em backend/main.py especificamente pra essa combinação de portas).
export const environment = {
  production: false,
  apiUrl: 'http://localhost:5050',
};
