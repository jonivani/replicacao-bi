# Frontend

Consulte ../README.md para instalação e arquitetura.

A versão exibida no rodapé vem de package.json e é incorporada ao compilar.
Para uma correção, execute nesta pasta:

```powershell
npm.cmd version patch --no-git-tag-version
npm.cmd run build
```

Use minor para novos recursos e major para mudanças incompatíveis.
O comando npm version mantém package.json e package-lock.json sincronizados.
