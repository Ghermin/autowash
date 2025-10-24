<template>
    <section class="projets container">
        <h1>Projets</h1>
        <p class="lead">Voici une collection de mes projets personnels et expérimentations.</p>

        <ul class="projects-grid">
            <li v-for="p in projects" :key="p.id" class="project-card">
                <h2 class="project-title">{{ p.title }}</h2>
                <p class="project-desc">{{ p.description }}</p>
                <p class="project-tech" v-if="p.tech">Tech: {{ p.tech.join(', ') }}</p>
                <a v-if="p.link" :href="p.link" target="_blank" rel="noopener" class="project-link">Voir</a>
            </li>
        </ul>

        <p class="note">Si vous voulez ajouter ou mettre à jour un projet, ouvrez une issue ou un PR.</p>
    </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const projects = ref([
    {
        id: 'portfolio',
        title: 'Portfolio personnel',
        description: "Ce site (hermin-geoffrey.fr) — construit avec Vue 3 + Vite. C'est un terrain d'expérimentation.",
        tech: ['Vue 3', 'Vite', 'TypeScript'],
        link: '/',
    },
    {
        id: 'example-app',
        title: 'Exemple d\'application',
        description: 'Petite application démontrant une feature intéressante.',
        tech: ['Node', 'Express'],
        link: null,
    },
])
</script>

<style scoped>
.container {
    max-width: 880px;
    margin: 0 auto;
    padding: 2rem 1rem;
}

.projects-grid {
    list-style: none;
    padding: 0;
    margin: 1.5rem 0;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 1rem;
}

.project-card {
    background: var(--card-bg, #fff);
    border: 1px solid rgba(0, 0, 0, 0.06);
    border-radius: 8px;
    padding: 1rem;
}

.project-title {
    margin: 0 0 .5rem 0;
    font-size: 1.05rem
}

.project-desc {
    margin: 0 0 .75rem 0;
    color: #374151
}

.project-link {
    color: #2563eb;
    text-decoration: none
}

.lead {
    color: #6b7280
}

.note {
    margin-top: 1.5rem;
    color: #6b7280;
    font-size: .9rem
}
</style>
