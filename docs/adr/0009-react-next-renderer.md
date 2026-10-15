# Renderer e interfaces em React/Next.js

A Proposal UI (link público) e a Admin UI são aplicações Next.js/React com TypeScript, separadas do core. Elas renderizam o conteúdo estruturado da ProposalVersion (Sections) a partir de templates; o PDF é gerado a partir do mesmo HTML com Chromium, para fidelidade entre web e PDF.

Rejeitamos renderizar no backend Python com templates de servidor porque a experiência interativa da proposta (tracking por seção, chat, aceite) e os Web Components embutíveis pedem um front-end rico.

Implementação: contexto registrado; código no M2 (Blueprint §41 e §42).
