// Produção — o build (`ng build`) é servido pelo próprio FastAPI (Fase 3a, tarefa 6 do
// roadmap v2), mesma origem, então a URL da API é relativa e não precisa de CORS.
export const environment = {
  production: true,
  apiUrl: '',
};
