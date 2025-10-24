import ftp from "basic-ftp";
import fs from "fs";
import path from "path";

const client = new ftp.Client();
client.ftp.verbose = true;

const localPath = path.join(process.cwd(), "dist");

async function uploadDir(localDir, remoteDir) {
    await client.ensureDir(remoteDir);
    const files = fs.readdirSync(localDir);

    for (const file of files) {
        const localFile = path.join(localDir, file);
        const remoteFile = path.posix.join(remoteDir, file);

        if (fs.statSync(localFile).isDirectory()) {
            if (["node_modules", ".git"].includes(file)) continue;
            await uploadDir(localFile, remoteFile);
        } else {
            await client.uploadFrom(localFile, remoteFile);
            console.log(`📤 Upload ${file}`);
        }
    }
}

// Check if we're running in GitHub Actions
const isCI = Boolean(process.env.GITHUB_ACTIONS);

(async () => {
    // Skip deploy when running locally
    if (!isCI) {
        console.log("ℹ️ Déploiement ignoré en local.");
        console.log("   Le déploiement se fera automatiquement à la fusion d'une PR sur main.");
        process.exit(0);
    }

    try {
        // Ces variables sont fournies par GitHub Actions via les secrets du repo
        const host = process.env.FTP_HOST;
        const user = process.env.FTP_USER;
        const password = process.env.FTP_PASSWORD;
        const targetRemotePath = process.env.FTP_REMOTE_PATH;
        const secureEnv = process.env.FTP_SECURE;

        if (!host || !user || !password || !targetRemotePath || typeof secureEnv === 'undefined') {
            throw new Error('Variables FTP manquantes dans les secrets GitHub Actions.');
        }

        const secure = String(secureEnv).toLowerCase() === 'true';

        console.log(`🚀 Connexion FTP à ${host}...`);
        await client.access({ host, user, password, secure });

        console.log("📂 Upload des fichiers...");
        await uploadDir(localPath, targetRemotePath);

        console.log("✅ Déploiement terminé !");
        client.close();
    } catch (err) {
        console.error("❌ Erreur FTP :", err);
    }
})();
