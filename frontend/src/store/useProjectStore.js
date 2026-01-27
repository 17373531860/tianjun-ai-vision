import { defineStore } from 'pinia';

export const useProjectStore = defineStore('project', {
  state: () => ({
    currentProjectId: null,
    currentProjectName: '未选择',
    currentProject: null, // Stores the full project object
    isRunning: false,
  }),
  actions: {
    setCurrentProject(project) {
      if (project) {
        this.currentProjectId = project.id;
        this.currentProjectName = project.name;
        this.currentProject = project;
      } else {
        this.currentProjectId = null;
        this.currentProjectName = '未选择';
        this.currentProject = null;
      }
    },
    setRunningStatus(status) {
      this.isRunning = status;
    }
  }
});